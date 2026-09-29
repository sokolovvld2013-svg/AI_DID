/**
 * Обезличивание DOCX в браузере.
 *
 * Файл — ZIP-контейнер, поэтому используются штатные Compression Streams
 * браузера: распаковка и упаковка идут через DecompressionStream /
 * CompressionStream с deflate-raw, без внешних библиотек.
 *
 * Ключевая сложность: текст в DOCX разбит на runs (<w:r>), и одно ФИО часто
 * разорвано границами форматирования («Ива|нов И.И.»). Поэтому сначала
 * собирается текст каждого абзаца, сущности находятся в нём целиком, а
 * результат затем разносится обратно по runs.
 */
(function (global) {
'use strict';

const ENCODER = new TextEncoder();
const DECODER = new TextDecoder('utf-8');

// ---------------------------------------------------------------------------
// ZIP
// ---------------------------------------------------------------------------

const SIG_LOCAL = 0x04034b50;
const SIG_CENTRAL = 0x02014b50;
const SIG_EOCD = 0x06054b50;

function findEndOfCentralDirectory(view, size) {
    const min = Math.max(0, size - 0xffff - 22);
    for (let i = size - 22; i >= min; i -= 1) {
        if (view.getUint32(i, true) === SIG_EOCD) return i;
    }
    return -1;
}

async function inflateRaw(data) {
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function deflateRaw(data) {
    const stream = new Blob([data]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

/** CRC-32 (IEEE 802.3), полином 0xedb88320. */
const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
        let c = i;
        for (let k = 0; k < 8; k += 1) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
        table[i] = c >>> 0;
    }
    return table;
})();

function crc32(data) {
    let crc = 0xffffffff;
    for (let i = 0; i < data.length; i += 1) {
        crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
    }
    return (crc ^ 0xffffffff) >>> 0;
}

/** Разбирает ZIP: возвращает список записей с распакованным содержимым. */
async function readZip(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const eocd = findEndOfCentralDirectory(view, bytes.byteLength);
    if (eocd < 0) throw new Error('Файл не является корректным DOCX (не найдена структура ZIP)');

    const total = view.getUint16(eocd + 10, true);
    let offset = view.getUint32(eocd + 16, true);
    const entries = [];

    for (let i = 0; i < total; i += 1) {
        if (view.getUint32(offset, true) !== SIG_CENTRAL) {
            throw new Error('Повреждена структура ZIP: неверная запись каталога');
        }
        const method = view.getUint16(offset + 10, true);
        const compSize = view.getUint32(offset + 20, true);
        const nameLen = view.getUint16(offset + 28, true);
        const extraLen = view.getUint16(offset + 30, true);
        const commentLen = view.getUint16(offset + 32, true);
        const localOffset = view.getUint32(offset + 42, true);
        const name = DECODER.decode(bytes.subarray(offset + 46, offset + 46 + nameLen));

        // Смещение данных — из локального заголовка: длина имени и extra там
        // может отличаться от каталога.
        const localNameLen = view.getUint16(localOffset + 26, true);
        const localExtraLen = view.getUint16(localOffset + 28, true);
        const dataStart = localOffset + 30 + localNameLen + localExtraLen;
        const raw = bytes.subarray(dataStart, dataStart + compSize);

        let data;
        if (method === 0) data = raw.slice();
        else if (method === 8) data = await inflateRaw(raw);
        else throw new Error('Неподдерживаемый метод сжатия ZIP: ' + method);

        entries.push({ name, data });
        offset += 46 + nameLen + extraLen + commentLen;
    }
    return entries;
}

/** Собирает ZIP обратно. Содержимое сжимается deflate, CRC пересчитывается. */
async function writeZip(entries) {
    const parts = [];
    const central = [];
    let offset = 0;

    for (const entry of entries) {
        const nameBytes = ENCODER.encode(entry.name);
        const compressed = await deflateRaw(entry.data);
        const sum = crc32(entry.data);

        const local = new DataView(new ArrayBuffer(30));
        local.setUint32(0, SIG_LOCAL, true);
        local.setUint16(4, 20, true);          // версия
        local.setUint16(6, 0x0800, true);      // флаг: имя в UTF-8
        local.setUint16(8, 8, true);           // метод: deflate
        local.setUint16(10, 0, true);          // время
        local.setUint16(12, 0x21, true);       // дата (1 января 1980)
        local.setUint32(14, sum, true);
        local.setUint32(18, compressed.length, true);
        local.setUint32(22, entry.data.length, true);
        local.setUint16(26, nameBytes.length, true);
        local.setUint16(28, 0, true);          // extra

        parts.push(new Uint8Array(local.buffer), nameBytes, compressed);

        const dir = new DataView(new ArrayBuffer(46));
        dir.setUint32(0, SIG_CENTRAL, true);
        dir.setUint16(4, 20, true);
        dir.setUint16(6, 20, true);
        dir.setUint16(8, 0x0800, true);
        dir.setUint16(10, 8, true);
        dir.setUint16(12, 0, true);
        dir.setUint16(14, 0x21, true);
        dir.setUint32(16, sum, true);
        dir.setUint32(20, compressed.length, true);
        dir.setUint32(24, entry.data.length, true);
        dir.setUint16(28, nameBytes.length, true);
        dir.setUint32(42, offset, true);
        central.push(new Uint8Array(dir.buffer), nameBytes);

        offset += 30 + nameBytes.length + compressed.length;
    }

    const centralSize = central.reduce((sum, p) => sum + p.length, 0);
    const eocd = new DataView(new ArrayBuffer(22));
    eocd.setUint32(0, SIG_EOCD, true);
    eocd.setUint16(8, entries.length, true);
    eocd.setUint16(10, entries.length, true);
    eocd.setUint32(12, centralSize, true);
    eocd.setUint32(16, offset, true);

    const all = [...parts, ...central, new Uint8Array(eocd.buffer)];
    const total = all.reduce((sum, p) => sum + p.length, 0);
    const out = new Uint8Array(total);
    let pos = 0;
    for (const p of all) { out.set(p, pos); pos += p.length; }
    return out;
}

// ---------------------------------------------------------------------------
// XML: абзацы и текстовые узлы
// ---------------------------------------------------------------------------

/** Раскодирование текстового узла: &amp; &lt; &gt; &quot; &apos; и &#NN; */
function decodeXmlText(s) {
    return s.replace(/&(#x?[0-9A-Fa-f]+|[a-z]+);/g, (all, body) => {
        if (body[0] === '#') {
            const code = body[1] === 'x' || body[1] === 'X'
                ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
            return Number.isFinite(code) ? String.fromCodePoint(code) : all;
        }
        const named = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
        return Object.prototype.hasOwnProperty.call(named, body) ? named[body] : all;
    });
}

const XML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' };

function encodeXmlText(s) {
    return s.replace(/[&<>"']/g, ch => XML_ESCAPES[ch]);
}

// Узлы, несущие текст: w:t — обычный, w:delText — внутри исправлений
// (зачёркнутый текст тоже содержит персональные данные и должен маскироваться).
const TEXT_NODE = /<w:(t|delText)(?:\s[^>]*)?>([\s\S]*?)<\/w:\1>|<w:(t|delText)(?:\s[^>]*)?\/>/g;
// Открывающий тег вынесен в группу: его нужно пересобрать дословно.
// (?=[\s>]) обязателен: иначе «<w:p>» совпадает как начало «<w:pPr>».
const PARAGRAPH = /(<w:p(?=[\s>])[^>]*>)([\s\S]*?)<\/w:p>|<w:p(?=[\s/>])[^>]*\/>/g;

/** Раскладывает XML абзаца на текстовые узлы и их границы в склеенном тексте. */
function collectRuns(paragraphXml) {
    const runs = [];
    let plain = '';
    TEXT_NODE.lastIndex = 0;
    let m;
    while ((m = TEXT_NODE.exec(paragraphXml)) !== null) {
        const tag = m[1] || m[3];
        const isVoid = m[1] === undefined;
        const body = isVoid ? '' : (m[2] || '');
        // Пропускаем инструкции полей (PAGE, TOC): их значения — служебные.
        if (!isVoid && body) {
            const start = plain.length;
            const text = decodeXmlText(body);
            plain += text;
            runs.push({ tag, start, end: plain.length, text, raw: body });
        } else {
            runs.push({ tag, start: plain.length, end: plain.length, text: '', raw: '' });
        }
    }
    return { runs, plain };
}

/**
 * Раскладывает результат маскирования обратно по текстовым узлам.
 *
 * Сущность может пересечь границы runs («Ива|нов»). Плейсхолдер вставляется
 * в узел, где сущность начинается, а покрытая часть остальных узлов
 * удаляется. Сущности обрабатываются справа налево, чтобы смещения не
 * «поехали».
 */
function applyMaskedText(runs, plain, spans) {
    if (!spans.length) return;
    for (let i = spans.length - 1; i >= 0; i -= 1) {
        const span = spans[i];
        let first = true;
        for (const run of runs) {
            if (run.end <= span.start || run.start >= span.end) continue;
            const from = Math.max(run.start, span.start);
            const to = Math.min(run.end, span.end);
            const localFrom = from - run.start;
            const localTo = to - run.start;
            if (first) {
                run.text = run.text.slice(0, localFrom) + span.text + run.text.slice(localTo);
                run.raw = encodeXmlText(run.text);
                first = false;
            } else {
                run.text = run.text.slice(0, localFrom) + run.text.slice(localTo);
                run.raw = encodeXmlText(run.text);
            }
        }
    }
}

/** Возвращает XML абзаца с подставленными текстовыми узлами. */
function rebuildParagraph(paragraphXml, runs) {
    let out = '';
    let cursor = 0;
    TEXT_NODE.lastIndex = 0;
    let m;
    while ((m = TEXT_NODE.exec(paragraphXml)) !== null) {
        const full = m[0];
        const isVoid = m[1] === undefined;
        const openTag = isVoid ? full : full.slice(0, full.indexOf('>') + 1);
        out += paragraphXml.slice(cursor, m.index) + openTag;
        if (!isVoid) {
            const run = runs.shift();
            out += run ? run.raw : (m[2] || '');
            out += '</w:' + (m[1] || m[3]) + '>';
        }
        cursor = m.index + full.length;
    }
    return out + paragraphXml.slice(cursor);
}

/** Обезличивает один XML-файл части документа общим реестром движка. */
function maskDocumentXml(xml, engine) {
    const records = [];
    let cursor = 0;
    PARAGRAPH.lastIndex = 0;
    let m;
    while ((m = PARAGRAPH.exec(xml)) !== null) {
        records.push({
            start: m.index,
            end: m.index + m[0].length,
            raw: m[0],
            open: m[1],
            body: m[2],
            isVoid: m[1] === undefined,
            prefix: xml.slice(cursor, m.index),
            runs: null,
            plain: '',
            crossRanges: [],
        });
        cursor = m.index + m[0].length;
    }

    for (const record of records) {
        if (!record.isVoid) {
            const parsed = collectRuns(record.body);
            record.runs = parsed.runs;
            record.plain = parsed.plain;
        }
    }

    // Word иногда разрывает одно наименование на два соседних абзаца:
    // «ФГУП «Дирекция по инвестиционной» / «деятельности»». Серверный
    // processor анализирует общий текст и маскирует оба сегмента; браузерный
    // путь должен сделать то же. Для пересекающего границу спана ставим один
    // и тот же placeholder в каждом из двух абзацев.
    for (let i = 0; i + 1 < records.length; i += 1) {
        const left = records[i];
        const right = records[i + 1];
        if (left.isVoid || right.isVoid || !left.plain.trim() || !right.plain.trim()) continue;

        const join = left.plain + '\n' + right.plain;
        const boundary = left.plain.length;
        const spans = engine.assign(engine.analyze(join));
        for (const span of spans) {
            if (span.start >= boundary || span.end <= boundary + 1) continue;
            applyMaskedText(left.runs, left.plain, [{
                start: span.start,
                end: boundary,
                text: span.text,
            }]);
            applyMaskedText(right.runs, right.plain, [{
                start: 0,
                end: span.end - boundary - 1,
                text: span.text,
            }]);
            left.crossRanges.push({ start: span.start, end: boundary });
            right.crossRanges.push({ start: 0, end: span.end - boundary - 1 });
        }
    }

    let out = '';
    for (const record of records) {
        out += record.prefix;
        if (record.isVoid) {
            out += record.raw;
            continue;
        }
        if (record.plain.trim()) {
            // Сохраняем остальные сущности этого же абзаца; повторно не
            // применяем только диапазон, уже заменённый через границу абзацев.
            const spans = engine.assign(engine.analyze(record.plain)).filter(span =>
                !record.crossRanges.some(range =>
                    span.start < range.end && range.start < span.end));
            applyMaskedText(record.runs, record.plain, spans);
        }
        out += record.open;
        out += rebuildParagraph(record.body, record.runs);
        out += '</w:p>';
    }
    return out + xml.slice(cursor);
}

// ---------------------------------------------------------------------------
// Публичный API
// ---------------------------------------------------------------------------

// Части документа, где может быть персональная информация.
const TEXT_PARTS = [
    /^word\/document\.xml$/,
    /^word\/footnotes\.xml$/,
    /^word\/endnotes\.xml$/,
    /^word\/header\d*\.xml$/,
    /^word\/footer\d*\.xml$/,
    /^word\/comments\.xml$/,
];

function isTextPart(name) {
    return TEXT_PARTS.some(re => re.test(name));
}

function isMetadataPart(name) {
    return name === 'docProps/core.xml' || name === 'docProps/app.xml';
}

/**
 * Обезличивает DOCX.
 *
 * @param {ArrayBuffer} buffer — содержимое файла.
 * @param {object} engine — экземпляр AnonymizeCore.createEngine.
 * @returns {Promise<{blob: Blob, report: object}>}
 */
async function anonymizeDocx(buffer, engine) {
    const bytes = new Uint8Array(buffer);
    const entries = await readZip(bytes);

    if (!entries.some(e => e.name === 'word/document.xml')) {
        throw new Error('В файле нет word/document.xml — это не DOCX');
    }

    const parts = [];
    for (const entry of entries) {
        if (isTextPart(entry.name)) {
            const xml = DECODER.decode(entry.data);
            const masked = maskDocumentXml(xml, engine);
            const encoded = ENCODER.encode(masked);
            if (encoded.length !== entry.data.length) {
                parts.push({ name: entry.name, data: encoded });
                continue;
            }
            parts.push({ name: entry.name, data: entry.data });
            continue;
        }
        if (isMetadataPart(entry.name)) {
            // Автор и последний редактор в свойствах файла — тоже персональные
            // данные, но не проходят через NER, поэтому очищаются напрямую.
            const xml = DECODER.decode(entry.data);
            const cleaned = xml
                .replace(/(<dc:creator[^>]*>)[\s\S]*?(<\/dc:creator>)/g, '$1$2')
                .replace(/(<cp:lastModifiedBy[^>]*>)[\s\S]*?(<\/cp:lastModifiedBy>)/g, '$1$2')
                .replace(/(<Company>)[\s\S]*?(<\/Company>)/g, '$1$2');
            parts.push({ name: entry.name, data: ENCODER.encode(cleaned) });
            continue;
        }
        parts.push(entry);
    }

    const zip = await writeZip(parts);
    return { blob: new Blob([zip], {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }), report: engine.report() };
}

const API = { anonymizeDocx, readZip, writeZip, crc32 };

if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (global) global.AnonymizeDocx = API;

})(typeof window !== 'undefined' ? window : null);
