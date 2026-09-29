/**
 * Обезличивание документов в браузере — ядро распознавания и маскирования.
 *
 * Назначение: документ не покидает компьютер сотрудника. Ни сети, ни сервера,
 * ни LLM — обработка целиком выполняется в этой вкладке.
 *
 * Порт secretary/anomizer/src/anomizer/ner.py + registry.py, поэтому правила
 * и плейсхолдеры совпадают с серверной версией. Отличия:
 *
 *   — вместо Natasha (ML-модель) и pymorphy2 используются регулярные выражения
 *     и лексикон фамилий/имён static/js/lex_names.js, выгруженный из того же
 *     словаря pymorphy2: без него любое слово на «-ов»/«-ин» считалось
 *     фамилией и в ФИО попадали «Объектов», «Актов», «Расходов»;
 *   — фамилии, которых нет в словаре (сервер угадывает их по окончанию),
 *     ловится по инициалам: «А.И. Козяйчев»;
 *   — реквизиты принимаются только при верной контрольной сумме (ИНН 10/12,
 *     СНИЛС, ОГРН/ОГРНИП), поэтому случайные числа не маскируются;
 *   — состав реквизитов шире: СНИЛС, ОГРН, ОКПО, КПП, БИК, расчётный счёт,
 *     паспорт, адрес, дата рождения, банковская карта. В серверной версии их
 *     не было вовсе, а именно они образуют основную часть идентифицирующих
 *     данных работника и контрагента.
 *
 * Точность наименований (PERSON) ниже, чем у Natasha: нераспознанные имена
 * и фамилии иностранного происхождения возможны. Режим «На сервере»
 * (Natasha + pymorphy2) остаётся запасным для таких случаев.
 */
(function (global) {
'use strict';

const PERSON = 'PERSON';
const ORG = 'ORG';

// Браузерный режим по умолчанию повторяет состав серверного отчёта.
// Расширенные типы (адреса, банковские реквизиты и т.д.) не маскируются здесь.
const SERVER_PARITY_TYPES = new Set([PERSON, ORG, 'INN', 'EMAIL', 'PHONE', 'PASSPORT', 'PERSNUM']);
// Сервер не включает в отчёт типовые реквизиты банковского шаблона и роль
// стороны договора, даже если у них есть ОПФ. Браузер следует тому же набору.
const SERVER_PARITY_ORG_REJECT = new Set(['сбербанк россии', 'арендатора']);

const TYPE_LABELS = {
    PERSON: 'ФИО',
    ORG: 'Организации',
    INN: 'ИНН',
    EMAIL: 'Email',
    PHONE: 'Телефон',
    SNILS: 'СНИЛС',
    OGRN: 'ОГРН / ОГРНИП',
    OKPO: 'ОКПО',
    KPP: 'КПП',
    BIK: 'БИК',
    ACCOUNT: 'Расчётный счёт',
    PASSPORT: 'Паспорт',
    PERSNUM: 'Персональный номер',
    ADDRESS: 'Адрес',
    BIRTHDATE: 'Дата рождения',
    CARD: 'Банковская карта',
};

// Порядок вывода в отчёте: сначала люди и организации, затем реквизиты.
const TYPE_ORDER = [
    'PERSON', 'ORG', 'INN', 'SNILS', 'OGRN', 'OKPO', 'KPP', 'BIK',
    'ACCOUNT', 'PASSPORT', 'PERSNUM', 'CARD', 'ADDRESS', 'BIRTHDATE', 'PHONE', 'EMAIL',
];

// ---------------------------------------------------------------------------
// Служебное
// ---------------------------------------------------------------------------

// Границы слова. В JavaScript \b опирается на латиницу, поэтому для кириллицы
// границы задаются явно.
const LB = '(?<![0-9A-Za-zА-Яа-яЁё_])';
const RB = '(?![0-9A-Za-zА-Яа-яЁё_])';

const PERSON_STOPWORDS = new Set([
    'инн', 'огрн', 'огрнип', 'октмо', 'окпо', 'кпп', 'снилс', 'бик', 'оквед',
    'уин', 'кбк', 'рс', 'кс', 'ооо', 'оао', 'зао', 'пао', 'ао', 'аон', 'гуп',
    'муп', 'фгуп', 'фгбу', 'ано', 'нко', 'дпо', 'оод', 'тсж', 'снт', 'дмп',
    'мп', 'ио', 'учредитель', 'заявитель', 'покупатель', 'арендатор',
    'арендодатель', 'подрядчик', 'заказчик', 'исполнитель', 'продавец',
    'контрагент',
]);

// Лексикон фамилий и имён (static/js/lex_names.js), тот же словарь, что на
// сервере. Без него любое слово на «-ов»/«-ин» считалось фамилией, и в ФИО
// попадали «Объектов», «Актов», «Расходов», «СПОРОВ».
const NAME_LEXICON = (
    (global && global.ANON_NAME_LEXICON)
    || (typeof globalThis !== 'undefined' && globalThis.ANON_NAME_LEXICON)
    || new Set()
);

// ОПФ и прочие служебные слова в наименовании юрлица: в канонический ключ
// не входят, чтобы «ООО "Ромашка"» и «Ромашка» давали один плейсхолдер.
const ORG_FORM_WORDS = new RegExp(
    LB + '(?:' +
    'обществ[ао]|открытое|закрытое|акционерное|публичное|непубличное|' +
    'государственное|муниципальное|унитарное|единое|ответственностью|' +
    'предприяти[ея]|ооо|оао|зао|пао|ао|аон|гуп|муп|фгуп|фгбу|фгкц|ано|нко|' +
    'дпо|оод|нк|тсж|снт|дмп|учреждение|организация' +
    ')' + RB, 'gi');

const ORG_FORM_PREFIX = new RegExp(
    '^(?:общество\\s+с\\s+ограниченн[а-яё]*\\s+ответственност[а-яё]*|' +
    '(?:федеральн[а-яё]*|государственн[а-яё]*|муниципальн[а-яё]*|' +
    'частн[а-яё]*|открыт[а-яё]*|закрыт[а-яё]*|публичн[а-яё]*|' +
    'непубличн[а-яё]*|акционерн[а-яё]*)\\s+)?' +
    '(?:общество|унитарн[а-яё]*\\s+предприяти[а-яё]*|предприяти[а-яё]*)?\\s*',
    'i');

const CYRILLIC = 'а-яё';

function escapeRegExp(s) {
    return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Канонический ключ телефона: только значащие цифры номера. */
function phoneCanon(text) {
    const head = String(text).split(/доб/i)[0];
    let digits = head.replace(/\D/g, '');
    if (digits.length === 11 && (digits[0] === '7' || digits[0] === '8')) {
        digits = digits.slice(1);
    }
    return digits;
}

/** Канонический ключ паспорта: только цифры серии и номера. */
function passportCanon(text) {
    return String(text).replace(/\D/g, '');
}

/** Канонический ключ персонального номера: только цифры. */
function personalNumCanon(text) {
    return String(text).replace(/\D/g, '');
}

/** Канонический ключ юрлица: ОПФ, регистр, «ё» и кавычки не влияют. */
function orgCanon(name) {
    let s = String(name).replace(/ё/g, 'е');
    s = s.replace(ORG_FORM_WORDS, ' ');
    s = s.replace(new RegExp('[^0-9A-Za-zА-Яа-яЕе\\s-]', 'g'), ' ');
    s = s.replace(/[\s_-]+/g, ' ').trim().toLowerCase();
    return s || String(name).trim().toLowerCase();
}

/** Сброс lastIndex и проверка: у глобальных регулярных выражений состояние. */
function reTest(re, text) {
    re.lastIndex = 0;
    const found = re.test(text);
    re.lastIndex = 0;
    return found;
}

// ---------------------------------------------------------------------------
// Контрольные суммы реквизитов
// ---------------------------------------------------------------------------

/** ИНН 10 знаков: сумма по разрядам 2,4,10,3,5,9,4,6,8. */
function validInn10(digits) {
    const w = [2, 4, 10, 3, 5, 9, 4, 6, 8];
    let sum = 0;
    for (let i = 0; i < 9; i += 1) sum += Number(digits[i]) * w[i];
    return (sum % 11) % 10 === Number(digits[9]);
}

/** ИНН 12 знаков: две контрольные суммы. */
function validInn12(digits) {
    const w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
    let s1 = 0;
    for (let i = 0; i < 10; i += 1) s1 += Number(digits[i]) * w1[i];
    if ((s1 % 11) % 10 !== Number(digits[10])) return false;
    const w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8];
    let s2 = 0;
    for (let i = 0; i < 11; i += 1) s2 += Number(digits[i]) * w2[i];
    return (s2 % 11) % 10 === Number(digits[11]);
}

/** СНИЛС: 9 цифр, первые 8 — номер, девятая — контрольная. */
function validSnils(digits) {
    if (digits.length !== 9) return false;
    let sum = 0;
    for (let i = 0; i < 8; i += 1) sum += Number(digits[i]) * (9 - i);
    const rest = sum % 101;
    // Остаток 100 допустим и означает контрольное число «00».
    return (rest === 100 ? 0 : rest % 10) === Number(digits[8]);
}

/** ОГРН 13 знаков: последняя цифра = (первые 12 mod 11) mod 10. */
function validOgrn13(digits) {
    let head = 0;
    for (let i = 0; i < 12; i += 1) head = (head * 10 + Number(digits[i])) % 11;
    return head % 10 === Number(digits[12]);
}

/** ОГРНИП 15 знаков: последние две = (первые 13 mod 11) mod 10. */
function validOgrnip15(digits) {
    let head = 0;
    for (let i = 0; i < 13; i += 1) head = (head * 10 + Number(digits[i])) % 11;
    return String(head % 10).padStart(2, '0') === digits.slice(13, 15);
}

// Обрезка адреса: конец предложения либо начало подписи следующего поля.
// Перед точкой обязательно не менее трёх строчных букв, иначе сокращения
// внутри адреса («г. Москва», «ул. Тверская», «д. 1») обрезали бы адрес.
const ADDRESS_TAIL = new RegExp(
    '(?<=[а-яё]{3})[.;!?]\\s+(?=[А-ЯЁ][а-яё])|' +
    ',?\\s*(?:Исполнитель|Директор|Генеральный|Ответственный|Представитель|' +
    'Паспорт|СНИЛС|ИНН|КПП|ОГРН|Телефон|тел\.|E-?mail|Ф\.И\.О\.)' +
    '(?![а-яё])', 'gi');

// Сокращения внутри адреса: после них точка не завершает предложение,
// поэтому «пер. Слезатьевский» не должен обрезаться до «пер».
const ADDRESS_ABBREV = new Set([
    'ул', 'пер', 'пр', 'просп', 'наб', 'ш', 'б', 'д', 'г', 'с', 'ст', 'кор',
    'корп', 'пом', 'кв', 'обл', 'мкр', 'пос', 'проезд', 'спуск', 'проул',
    'уч', 'стр', 'дом',
]);

function trimAddress(raw) {
    let value = String(raw);
    for (;;) {
        ADDRESS_TAIL.lastIndex = 0;
        const stop = ADDRESS_TAIL.exec(value);
        if (!stop) break;
        const before = value.slice(0, stop.index);
        const word = (before.match(/([А-Яа-яЁё]+)$/) || ['', ''])[1].toLowerCase();
        if (ADDRESS_ABBREV.has(word)) {
            // Продолжаем искать следующую границу после сокращения.
            ADDRESS_TAIL.lastIndex = stop.index + stop[0].length;
            const next = ADDRESS_TAIL.exec(value);
            if (!next) break;
            value = value.slice(0, next.index);
            break;
        }
        value = before;
        break;
    }
    return value.replace(/[\s,;.]+$/, '').trim();
}

// Значение адреса должно выглядеть адресом. Без этой проверки метка
// «по адресу» захватывает любой хвост строки: «по адресу электронной почты:
// torgi@…» — это email, а не адрес; «адрес места нахождения Объекта
// аренды» — обрывок пункта договора.
const ADDRESS_NUMBER = /\d/;
const ADDRESS_POSTAL = /(?<![\d-])\d{6}(?!\d)/;
// \b в JavaScript опирается на латиницу, поэтому границы задаём явно (LB/RB).
const ADDRESS_GEO = new RegExp(
    '(?:' + LB + '(?:г|с|д|пос|ст|ул|просп|пр-т|пер|ш|б-р|наб|мкр|кв|пом|корп)\\.'
    + '|область|обл\\.|край|республик|район|р-н|город)' + RB, 'i');
const ADDRESS_STREET = new RegExp(
    '(?:' + LB + '(?:ул\\.|улиц|просп|проспект|пер\\.|переулок|шоссе|ш\\.|наб\\.|'
    + 'набережн|б-р\\.|бульвар|переулок))', 'i');

/** Похоже ли значение на почтовый адрес, а не на обрывок текста. */
function looksLikeAddress(value) {
    const v = String(value);
    if (v.length < 10 || v.length > 160) return false;
    // Адрес не содержит ни почты, ни ссылки, ни телефона.
    if (/@|https?:|www\.|тел\.|факс/i.test(v)) return false;
    // Адрес начинается с местности, а не с длинной вводной части: «на
    // Официальном сайте … 2021 г.» — это не адрес, даже если дальше есть
    // «г.» и шестизначное число.
    if (ADDRESS_POSTAL.test(v.slice(0, 45))) return true;
    if (!ADDRESS_NUMBER.test(v)) return false;
    if (ADDRESS_STREET.test(v.slice(0, 45))) return true;
    return ADDRESS_GEO.test(v.slice(0, 45));
}

// ---------------------------------------------------------------------------
// Детерминированные реквизиты
// ---------------------------------------------------------------------------
//
// group — номер группы, которую нужно замаскировать: для «КПП: 770123456»
// маскируется значение, а не подпись поля.
// check — проверка контрольной суммы, отсекающая случайные числа.

const REQUISITES = [
    {
        type: 'EMAIL',
        // Сервер сохраняет начальную точку в записи «.fgup-info@…» как часть
        // email. Разрешаем её только после несловного символа, чтобы не
        // захватывать точку из обычного предложения.
        re: new RegExp('(?<![\\w+-])(?:\\.)?[\\w%+-][\\w.%+-]*@[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)+(?![\\w-])', 'g'),
    },
    {
        type: 'PHONE',
        re: new RegExp(
            '(?<![\\d-])(?:' +
            '(?:\\+7|8)[\\s-]?\\(?\\d{3}\\)?[\\s-]?\\d{3}[\\s-]?\\d{2}[\\s-]?\\d{2}' +
            '|\\(\\d{3}\\)[\\s-]?\\d{3}[\\s-]?\\d{2}[\\s-]?\\d{2}' +
            '|\\d{3}[\\s-]\\d{3}[\\s-]\\d{2}[\\s-]\\d{2}' +
            ')(?![\\d-])(?:\\s*(?:доб|доб\\.|добление)\\s*\\d+)?(?![\\d-])', 'g'),
        canon: phoneCanon,
    },
    { type: 'ACCOUNT', re: /(?<!\d)[04]\d{19}(?!\d)/g },
    { type: 'OGRN', re: /(?<!\d)\d{15}(?!\d)/g, check: validOgrnip15 },
    { type: 'OGRN', re: /(?<!\d)\d{13}(?!\d)/g, check: validOgrn13 },
    { type: 'INN', re: /(?<!\d)\d{12}(?!\d)/g, check: validInn12 },
    { type: 'INN', re: /(?<!\d)\d{10}(?!\d)/g, check: validInn10 },
    { type: 'BIK', re: /(?<!\d)0[45]\d{7}(?!\d)/g },
    { type: 'CARD', re: /(?<!\d)(?:\d[ -]?){15}\d(?!\d)/g },
    { type: 'SNILS', re: /(?<!\d)\d{3}-\d{3}-\d{3}(?!\d)/g, check: validSnils },
    // Поля, у которых значение опознаётся по подписи слева.
    {
        type: 'INN',
        re: /ИНН\s*[:№]?\s*(\d{10}(?:\s?\d{2})?(?!\d))/gi,
        group: 1,
        check: (raw) => {
            const d = raw.replace(/\D/g, '');
            return d.length === 10 ? validInn10(d) : validInn12(d);
        },
    },
    {
        type: 'SNILS',
        re: /СНИЛС\s*[:№]?\s*(\d{3}[- ]?\d{3}[- ]?\d{3})(?!\d)/gi,
        group: 1,
        check: (raw) => validSnils(raw.replace(/\D/g, '')),
    },
    {
        type: 'KPP',
        // «КПП: 771801001» и слитная форма «ИНН/КПП 5032034971/771801001».
        re: /КПП\s*[:№]?\s*(?:\d{10}\s*\/\s*)?(\d{9})(?!\d)/gi,
        group: 1,
    },
    { type: 'OKPO', re: /ОКПО\s*[:№]?\s*(\d{8,10})(?!\d)/gi, group: 1 },
    {
        type: 'PASSPORT',
        // Серия (4 цифры) и номер (6 цифр); между ними возможен разделитель
        // бланка: «серия 50 20 № 123456», «сер. 5020 номером 123456».
        // Либо просто номер из 6-8 цифр — загранпаспорт без серии:
        // «Паспорт: № 25382441».
        re: /(?:паспорт[а-яё]*|удостоверени[ея]\s+личности)[^\n]{0,20}?(\d{2}[ ]?\d{2}(?:[ ]?(?:№|номер[а-яё]*|N\.?))?[ ]?\d{6}|[№N]?\s*\d{6,8})(?!\d)/gi,
        group: 1,
        canon: passportCanon,
    },
    {
        type: 'PERSNUM',
        // Персональный номер (зарубежный идентификатор): «Персональный номер:
        // 39307150323». Маскируются только цифры, подпись поля остаётся.
        re: /персональн[а-яё]*\s+номер[а-яё]*[^\n]{0,10}?(\d(?:[ ]?\d){10})(?!\d)/gi,
        group: 1,
        canon: personalNumCanon,
    },
    {
        type: 'BIRTHDATE',
        re: /(?:дата\s+рождения|д\.\s*р\.|родил[а-яё]*)\s*[:№]?\s*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4})/gi,
        group: 1,
    },
    {
        // Адрес захватывается до конца строки, затем обрезается по границам
        // предложения и подписям полей: «по адресу: ул. Тверская, д. 1,
        // кв. 5 Исполнитель: Иванов» должен дать адрес, а не хвост.
        type: 'ADDRESS',
        re: new RegExp(
            '(?:по\\s+адресу|адрес(?:\\s+места\\s+жительства|\\s+места\\s+регистрации|\\s+регистрации|\\s+местонахождения)?|' +
            'место\\s+жительства|место\\s+регистрации|местонахождени[ея](\\s+объекта)?|' +
            'проживающ[ийаяе]|зарегистрирован[а-яё]*|индекс)' +
            '\\s*[:№]?\\s*([^\\n]{5,200})', 'gi'),
        group: 2,
        trim: trimAddress,
        check: (raw) => looksLikeAddress(trimAddress(raw)),
    },
];

// Флаг d даёт доступ к индексам групп (m.indices): без него нельзя замаскировать
// значение поля, не затерев его подпись («КПП: 770123456»).
for (const item of REQUISITES) {
    if (item.re.flags.indexOf('d') === -1) {
        item.re = new RegExp(item.re.source, item.re.flags + 'd');
    }
}

// ---------------------------------------------------------------------------
// Персоны
// ---------------------------------------------------------------------------

const GIVEN_NAMES = new Set([
    'александр', 'алексей', 'алина', 'алиса', 'андрей', 'анна', 'антон',
    'аркадий', 'арсений', 'артем', 'артём', 'богдан', 'борис', 'вадим',
    'валентин', 'валерий', 'василий', 'виктор', 'виктория', 'виталий',
    'владимир', 'владислав', 'вячеслав', 'геннадий', 'георгий', 'григорий',
    'даниил', 'дарья', 'денис', 'дмитрий', 'евгений', 'елена', 'емельян',
    'ефим', 'иван', 'игорь', 'илья', 'кирилл', 'константин', 'лев', 'леонид',
    'максим', 'марк', 'марина', 'мария', 'матвей', 'михаил', 'никита',
    'николай', 'олег', 'олина', 'павел', 'пётр', 'петр', 'роман', 'руслан',
    'семён', 'сергей', 'степан', 'татьяна', 'тимур', 'тодор', 'фёдор',
    'федор', 'эдуард', 'юрий', 'ярослав',
    'алевтина', 'алла', 'анастасия', 'ангелина', 'антонина', 'арина', 'варвара',
    'василиса', 'вера', 'вероника', 'галина', 'диана', 'евгения', 'екатерина',
    'елизавета', 'жанна', 'зоя', 'инна', 'ирина', 'ксения', 'лариса', 'лидия',
    'любовь', 'людмила', 'маргарита', 'надежда', 'наталия', 'наталья',
    'нелли', 'нина', 'нинель', 'ольга', 'павлина', 'полина', 'раиса', 'регина',
    'светлана', 'софия', 'тамара', 'улита', 'элина', 'эльвира', 'юлия', 'яна',
    'ярослава',
]);

// Отчество: основа от 4 букв плюс одно из характерных окончаний.
const PATRONYMIC = /(?:ович|евич|иничн[аы]|ичн[аы]|овн[аы]|евн[аы]|и[ей]н[аы]?)$/;

// Окончания фамилий. Обрезая слово до основы, мы отсекаем имена в
// родительном и дательном падеже («Иванову», «Ивановым»).
const SURNAME = /(?:ов|ев|ёв|ин|ын|ий|ый|ая|ская|цкий|ская|цкая|ук|юк|швили|штейн)$/;

// Окончания прилагательных: «торговый», «ленинградский», «российский» —
// это названия и описания, а не фамилии. В составе ФИО они допустимы
// («Иванов Иванович» проверяется по отчеству), но одиночные слова с ними
// персоной не считаются.
const ADJECTIVE_ENDING = /(?:ый|ий|ая|яя|ое|ее|ые|ие|ой|ей|ого|его|ому|ему|ых|их)$/;

const MIN_PERSON_WORD_LEN = 2;

// Слова бланков и служебные. После инициалов они фамилией быть не могут:
// «М.П. Приложение», «И.О. Директор», «А.И. Сторона».
const ANCHOR_BLOCKLIST = new Set([
    'мп', 'ио', 'ооо', 'оао', 'зао', 'пао', 'ао', 'фгуп', 'фгбу', 'гуп', 'муп',
    'ано', 'нко', 'акт', 'приложение', 'приложения', 'директор', 'директора',
    'генеральный', 'коммерческий', 'управляющий', 'представитель', 'представителя',
    'сторона', 'стороны', 'исполнитель', 'исполнителя', 'заказчик', 'арендатор',
    'арендодатель', 'подрядчик', 'продавец', 'покупатель', 'контрагент',
    'учредитель', 'заявитель', 'руководитель', 'главный', 'бухгалтер',
    'секретарь', 'помощник', 'доверенность', 'доверенности', 'договор',
    'контракт', 'приказ', 'распоряжение', 'постановление', 'соглашение',
    'спецификация', 'ведомость', 'актсверки', 'заявка', 'жалоба', 'претензия',
    'иск', 'залог', 'аванс', 'вознаграждение', 'услуга', 'услуги', 'работа',
    'работы', 'товар', 'товары', 'имущество', 'помещение', 'здание', 'земля',
    'содержание', 'эксплуатация', 'ремонт', ' коммунальные', 'платеж', 'платежи',
]);

// Слова, после инициалов читающиеся как фамилия: длинные, заглавные,
// с характерным окончанием и не из бланка. Так ловится «А.И. Козяйчев» —
// фамилии, которой нет в словаре pymorphy2.
const ANCHOR_SUFFIX = /(?:ов|ев|ёв|ин|ын|ий|ук|юк|кo|ко|ян|им|ов)$/;

/** Инициалы как признак ФИО, когда слово после них в словарь не попало. */
function initialsAnchorAccepts(nameText) {
    if (NAME_LEXICON.size === 0) return false;
    // Инициалы должны быть в начале: «К хозяйчев А.И.» ловится отдельно.
    if (!/^[А-ЯЁ]\.[А-ЯЁ]?\.\s*/.test(nameText)) return false;
    const rest = nameText.replace(/^[А-ЯЁ]\.[А-ЯЁ]?\.\s*/, '');
    const words = rest.split(/\s+/).filter(Boolean);
    if (words.length !== 1) return false;
    const word = words[0].replace(LETTERS_RE, '').toLowerCase().replace(/ё/g, 'е');
    if (word.length < 5) return false;
    if (ANCHOR_BLOCKLIST.has(word) || PERSON_STOPWORDS.has(word)) return false;
    if (NAME_LEXICON.has(word)) return true;
    return ANCHOR_SUFFIX.test(word);
}

// Падежные окончания, снимаемые с конца слова перед проверкой.
// По убыванию длины, чтобы «ыми» снималось раньше «и».
const CASE_ENDINGS = [
    'ыми', 'ими', 'ями', 'ами', 'ого', 'его', 'ому', 'ему', 'ых', 'их',
    'ах', 'ях', 'ов', 'ев', 'ёв', 'ей', 'ой', 'ый', 'ая', 'ое', 'ые', 'ий',
    'ам', 'ям', 'ом', 'ем', 'ую', 'юю', 'у', 'ю', 'а', 'я', 'ы', 'и', 'о',
    'е', 'ь', 'й', 'ъ',
];

const PERSON_STRIP = " \t\n\r,.;:!?()«»\"“”„'‑-";
const PERSON_STRIP_RE = new RegExp(
    '^(?:[' + escapeRegExp(PERSON_STRIP) + ']+)|(?:[' + escapeRegExp(PERSON_STRIP) + ']+)$', 'g');

const LETTERS_RE = new RegExp('[^A-Za-zА-Яа-яЁё]', 'g');

function stems(word) {
    const out = [word];
    for (const end of CASE_ENDINGS) {
        if (word.length - end.length >= 3 && word.endsWith(end)) {
            out.push(word.slice(0, word.length - end.length));
        }
    }
    return out;
}

/** Слово является фамилией/именем/отчеством. */
function isSimplePersonToken(token) {
    const letters = token.replace(LETTERS_RE, '');
    if (letters.length < MIN_PERSON_WORD_LEN) return false;
    const low = letters.toLowerCase().replace(/ё/g, 'е');
    if (PERSON_STOPWORDS.has(low)) return false;
    for (const st of stems(low)) {
        if (GIVEN_NAMES.has(st)) return true;
        if (st.length >= 6 && PATRONYMIC.test(st)) return true;
        // Фамилия — только по лексикону. Одних окончаний «-ов»/«-ин»
        // мало: ими делятся «Объектов», «Актов», «Расходов», «Арендаторов».
        // Основу короче 4 букв не берём: снятие окончания даёт «Полы»→«пол».
        if (NAME_LEXICON.size > 0 && st.length >= 4 && NAME_LEXICON.has(st)) return true;
        // Без лексикона (файл не подключён) оставляем прежнее поведение.
        if (NAME_LEXICON.size === 0 && st.length >= 4 && SURNAME.test(st)) return true;
    }
    return false;
}

function isPersonWord(word) {
    const core = word.replace(PERSON_STRIP_RE, '');
    if (!core) return false;
    if (core.indexOf('-') >= 0) {
        // «Иванов-Ильин» — двойная фамилия, «Стороной-адресатом» — нет.
        const parts = core.split('-').filter(Boolean);
        return parts.length > 0 && parts.every(isSimplePersonToken);
    }
    return isSimplePersonToken(core);
}

function isLikelyPersonName(text) {
    const words = String(text).split(/\s+/).filter(Boolean);
    if (words.length < 2 || words.length > 3) return false;
    return words.every(isPersonWord);
}

// Фамилия после «ул.» или «улица» — обычно часть адреса, а не ФИО:
// «г. Благовещенск, ул. Шевченко, д. 85». Сервер не маскирует такие
// названия улиц, поэтому browser-путь делает ту же проверку контекста.
const STREET_NAME_PREFIX = new RegExp(
    '(?:^|[^0-9A-Za-zА-Яа-яЁё])(?:ул(?:ица)?|пер(?:еулок)?|просп(?:ект)?|'
    + 'пр-?т|ш(?:оссе)?|наб(?:ережная)?|б-?р|бульвар|проезд|пл(?:ощадь)?)\\.?\\s*$', 'i');

function isStreetNameContext(text, start) {
    return STREET_NAME_PREFIX.test(String(text).slice(Math.max(0, start - 64), start));
}

// «И.О. Иванов», «Иванов И.И.», «Е.В. Иночкин»
const INITIALS_SPAN = new RegExp(
    '(?:[А-ЯЁ]\\.\\s*[А-ЯЁ]\\.\\s*[А-ЯЁ][а-яё]+' +
    '|[А-ЯЁ][а-яё]+\\s+(?:[А-ЯЁ]\\.)+[А-ЯЁ]?)' +
    '(?![0-9A-Za-zА-Яа-яЁё])', 'g');

const WORD_TOKEN = /[А-ЯЁа-яёA-Za-z]+[.]*/g;
const PERSON_TAIL_MARKERS = new Set(['мп']);
const PERSON_TAIL_STRIP = new Set([' ', '\t', '\n', '\r', ',', ';', ':']);

/** Отрезает от конца ФИО маркер «М.П.» и обычные слова после имени. */
function trimPersonTail(text, start, end) {
    let e = end;
    for (;;) {
        const m = /\s*(\S+)\s*$/.exec(text.slice(start, e));
        if (!m) break;
        const letters = m[1].replace(/[^А-Яа-яЁё]/g, '').toLowerCase();
        if (PERSON_TAIL_MARKERS.has(letters)) e = start + m.index;
        else break;
    }
    const tokens = [];
    WORD_TOKEN.lastIndex = 0;
    let m;
    while ((m = WORD_TOKEN.exec(text.slice(start, e))) !== null) {
        tokens.push({ text: m[0], index: m.index });
    }
    if (!tokens.length) return { start, end: e };

    // От конца к началу: первое слово-имя завершает отсечение. Слово короче
    // MIN_PERSON_WORD_LEN (одиночная буква инициала) отсекать нельзя —
    // иначе «Иванов И.И.» потерял бы инициалы.
    let cut = null;
    let reachedBreak = false;
    for (let i = tokens.length - 1; i >= 0; i -= 1) {
        const core = tokens[i].text.replace(/\.+$/, '');
        if (isPersonWord(core)) { reachedBreak = true; break; }
        if (core.replace(LETTERS_RE, '').length >= MIN_PERSON_WORD_LEN && cut === null) {
            cut = tokens[i].index;
        }
    }
    if (!reachedBreak || cut === null) return { start, end: e };

    let endCut = start + cut;
    while (endCut > start && PERSON_TAIL_STRIP.has(text[endCut - 1])) endCut -= 1;
    return { start, end: endCut };
}

// ---------------------------------------------------------------------------
// Организации
// ---------------------------------------------------------------------------

const QUOTE_OPEN = '[«"„“]';
const QUOTE_CLOSE = '[»"”]';
const QUOTE_INNER = '[^«»"“”„\\n]';

// Полная ОПФ + наименование в кавычках: «Общество с ограниченной
// ответственностью "ДЕЗ"».
const LEGAL_ENTITY = new RegExp(
    '(?:' +
    'обществ[а-яё]*\\s+с\\s+ограниченн[а-яё]*\\s+ответственност[а-яё]*' +
    '|(?:открыт[а-яё]*|закрыт[а-яё]*|публичн[а-яё]*|непубличн[а-яё]*)?\\s*' +
    'акционерн[а-яё]*\\s+обществ[а-яё]*' +
    '|(?:федеральн[а-яё]*\\s+)?государственн[а-яё]*\\s+унитарн[а-яё]*\\s+предприяти[а-яё]*' +
    '|муниципальн[а-яё]*\\s+унитарн[а-яё]*\\s+предприяти[а-яё]*' +
    ')\\s*(' + QUOTE_OPEN + ')(' + QUOTE_INNER + '{2,60}?)(' + QUOTE_CLOSE + ')', 'gi');

// Сокращённая ОПФ + наименование: «ООО "ДЕЗ"», «АО "Ромашка"», «ФГУП "ДИД"».
// Длинные альтернативы идут первыми: иначе «АО» перехватит начало «АООН»,
// а «НП» — начало «НКО», и шаблон перестанет совпадать.
const ORG_ABBREV_LIST = [
    'ФГУП', 'ФГБУ', 'ФГАУ', 'ФГКЦ', 'ООО', 'ОАО', 'ЗАО', 'ПАО', 'АООН', 'АНО',
    'НКО', 'ОДО', 'НП', 'АО', 'ГУП', 'МУП', 'ГБУ', 'ГАУ', 'ТСЖ', 'СНТ', 'ДНП', 'ЖСК',
];

const LEGAL_ABBREV = new RegExp(
    '(?<![0-9A-Za-zА-Яа-яЁё_])' +
    '(?:' + ORG_ABBREV_LIST.join('|') + ')' +
    '(?![0-9A-Za-zА-Яа-яЁё_])\\s*(' + QUOTE_OPEN + ')(' + QUOTE_INNER + '{2,60}?)('
    + QUOTE_CLOSE + ')', 'gi');

// Полные формы ОПФ, которые пишут словами и без сокращения:
// «Федеральное государственное бюджетное учреждение», «Акционерное общество».
// Без флага i, поэтому первая буква задаётся явно: предложение может
// начинаться с «Федеральное», а в середине фразу пишут со строчной.
const ORG_FORM_FULL_LIST = [
    '[Фф]едеральн[а-яё]*\\s+государственн[а-яё]*\\s+бюджетн[а-яё]*\\s+учреждени[а-яё]*',
    '[Фф]едеральн[а-яё]*\\s+государственн[а-яё]*\\s+автономн[а-яё]*\\s+учреждени[а-яё]*',
    '(?:[Фф]едеральн[а-яё]*\\s+)?[Гг]осударственн[а-яё]*\\s+учреждени[а-яё]*',
    '[Мм]униципальн[а-яё]*\\s+учреждени[а-яё]*',
    '[Оо]бществ[а-яё]*\\s+с\\s+ограниченн[а-яё]*\\s+ответственност[а-яё]*',
    '(?:(?:[Пп]убличн[а-яё]*|[Нн]епубличн[а-яё]*|[Оо]ткрыт[а-яё]*|[Зз]акрыт[а-яё]*)\\s+)?'
        + '[Аа]кционерн[а-яё]*\\s+[Оо]бществ[а-яё]*',
    '(?:[Фф]едеральн[а-яё]*\\s+)?[Гг]осударственн[а-яё]*\\s+унитарн[а-яё]*\\s+предприяти[а-яё]*',
    '[Мм]униципальн[а-яё]*\\s+унитарн[а-яё]*\\s+предприяти[а-яё]*',
];

// ОПФ + наименование в кавычках, любой регистр: «ООО "ДЕЗ"».
// Именованные группы нужны extractOrgs, чтобы одинаково разбирать
// оба варианта записи.
const ORG_FORM_QUOTED = new RegExp(
    '(?<![0-9A-Za-zА-Яа-яЁё_])'
    + '(?:' + [...ORG_ABBREV_LIST, ...ORG_FORM_FULL_LIST].join('|') + ')'
    + '(?![0-9A-Za-zА-Яа-яЁё_])'
    + '\\s*(?<open>' + QUOTE_OPEN + ')(?<name>' + QUOTE_INNER + '{2,60}?)(?<close>' + QUOTE_CLOSE
    + ')', 'giu');

// ОПФ + наименование без кавычек: «ФГБУ ДОД», «ООО Ромашка-Авто».
// Без флага i намеренно: он сделал бы [А-ЯЁ] регистронезависимым, и правило
// стало бы съедать «АО вправе требовать исполнения».
// Не больше двух слов с заглавной буквы, иначе захватывается начало
// следующего предложения.
const BARE_ORG_NAME = '[А-ЯЁ][а-яёА-ЯЁ\\d]*(?:[-–—][а-яёА-ЯЁ\\d]+|\\.[а-яёА-ЯЁ\\d]+)*'
    + '(?:\\s+[А-ЯЁ][а-яёА-ЯЁ\\d]*(?:[-–—][а-яёА-ЯЁ\\d]+|\\.[а-яёА-ЯЁ\\d]+)*)?';

const ORG_FORM_BARE = new RegExp(
    '(?<![0-9A-Za-zА-Яа-яЁё_])'
    + '(?:' + [...ORG_ABBREV_LIST, ...ORG_FORM_FULL_LIST].join('|') + ')'
    + '(?![0-9A-Za-zА-Яа-яЁё_])'
    + '\\s+(?![«"„“])(?<name>' + BARE_ORG_NAME + ')', 'gu');

// Реквизиты и повторная ОПФ не являются частью наименования:
// «АО Сбербанк ИНН 7707083893» → «АО Сбербанк».
const ORG_NAME_TAIL_REJECT = new RegExp(
    '^(?:инн|кпп|огрн|огрнип|окпо|октмо|снилс|бик|уин|кбк|рс|кс|оао|ооо|зао|пао|'
    + 'ао|аон|гуп|муп|фгуп|фгбу|ано|нко|ип|флп|в|лице)$', 'i');

/** Убирает из наименования без кавычек хвост из реквизитов и ФИО. */
function trimBareOrgName(raw) {
    const words = String(raw).split(/\s+/).filter(Boolean);
    while (words.length > 1) {
        const last = words[words.length - 1];
        if (ORG_NAME_TAIL_REJECT.test(last) || isPersonWord(last)) words.pop();
        else break;
    }
    if (!words.length || ORG_NAME_TAIL_REJECT.test(words[0])) return '';
    return words.join(' ');
}

const QUOTED_NAME = new RegExp(
    QUOTE_OPEN + '[^«»"“”]*[А-Яа-яЁё][^«»"“”]*' + QUOTE_CLOSE, 'g');

// Сокращение организации в скобках сразу после полного наименования:
// АО «Сбербанк-АСТ» («Сбербанк-АСТ»).
const PAREN_ALIAS = /\s*\(\s*([«"„“][^»"”]*[А-Яа-яЁё][^»"”]*[»"”])\s*\)/;

// «…, именуемое в дальнейшем "Заказчик"» — вторая сторона договора.
const ORG_DECL = new RegExp(
    '(?<![0-9A-Za-zА-Яа-яЁё_])' +
    '([А-ЯЁ«][^;,\\n]{2,}?)\\s*,?\\s+именуем\\w*\\s+в\\s+дальнейшем\\s+' +
    '[«"„“][^»"”]*?[»"”]', 'g');

const ORG_DECL_REJECT = new RegExp(
    '^\\W*(?:инн|огрн|октмо|окпо|кпп|снилс|бик|оквед|уин|кбк|' +
    'заказчик|арендодатель|арендатор|исполнитель|подрядчик|покупатель|' +
    'продавец|заявитель|сторона|контрагент)' + RB, 'i');

const DID_NAME = 'дирекция\\s+по\\s+инвестиционной\\s+деятельности';
const DID_GAP = '[\\s«"„“]*';
const DID_TAIL = '[»"”]*';

const ORG_PATTERN_PARTS = [
    'федеральн[а-яё]*\\s+государственн[а-яё]*\\s+унитарн[а-яё]*\\s+предприяти[а-яё]*'
        + DID_GAP + '(?:' + DID_NAME + '|дид)' + DID_TAIL,
    'фгуп' + DID_GAP + '(?:' + DID_NAME + '|дид)' + DID_TAIL,
    '(?:[«»"“”])' + DID_NAME + '(?:[«»"“”])?',
    LB + 'дид' + RB,
];

const ROSUIM = new RegExp(
    '(?:' +
    '(?:федеральн[а-яё]*\\s+)?агентств[а-яё]*\\s+по\\s+управлению\\s+' +
    'государственн[а-яё]*\\s+имуществ[а-яё]*' +
    '|росимуществ[а-яё]*' +
    ')', 'gi');

const DID_ORG_CANON = orgCanon('ФГУП «Дирекция по инвестиционной деятельности»');
const ROSUIM_CANON = orgCanon(
    'Федеральное агентство по управлению государственным имуществом (Росимущество)');
// Сервер собирает название ДИД из соседних DOCX-абзацев и показывает в отчёте
// эту полную форму. В браузере абзацы маскируются отдельно, поэтому для
// согласованного отчёта закрепляем тот же отображаемый вариант.
const SERVER_PARITY_DID_DISPLAY =
    'Федерального государственного унитарного предприятия «Дирекция по инвестиционной деятельности»';

/** Варианты написания наименования компании из настроек приложения. */
function companyPatternParts(companyName) {
    if (!companyName) return [];
    const variants = new Set([companyName]);
    const quoted = /([«"„“])([^«»"“”]{2,})[»"”]/.exec(companyName);
    if (quoted) variants.add(quoted[2]);
    const bare = companyName.replace(ORG_FORM_PREFIX, '').trim(' \t«»""„“');
    if (bare.length >= 3) variants.add(bare);
    const parts = [];
    for (const variant of variants) {
        const words = variant.trim().split(/\s+/).filter(Boolean).map(escapeRegExp);
        if (words.length) parts.push(words.join('[\\s]+'));
    }
    return parts.sort((a, b) => b.length - a.length);
}

function buildCompanyPattern(companyName) {
    const parts = [...companyPatternParts(companyName), ...ORG_PATTERN_PARTS].filter(Boolean);
    return new RegExp('(?:' + parts.join('|') + ')', 'gi');
}

/** Регулярное выражение для произвольного запроса организации. */
function orgQueryPattern(org) {
    // Кавычки («», "", “”) считаем взаимозаменяемыми — одним проходом.
    const tokens = org.split(/\s+/).filter(Boolean)
        .map(t => escapeRegExp(t).replace(/[«»"“”]/g, '[«»"“”]'));
    return new RegExp('\\s*'.join(tokens), 'gi');
}

function looksLikeOrgName(name) {
    if (reTest(LEGAL_ENTITY, name) || reTest(LEGAL_ABBREV, name)) return true;
    return reTest(QUOTED_NAME, name) || isLikelyPersonName(name);
}

function looksLikeOrgAlias(alias) {
    const a = alias.trim();
    if (a.length < 4) return false;
    if (reTest(LEGAL_ENTITY, a) || reTest(LEGAL_ABBREV, a)) return true;
    const words = /[А-ЯЁа-яё]+/g;
    if (!words.exec(a)) return false;
    let count = 0;
    let m;
    while ((m = words.exec(a)) !== null) {
        const first = m[0][0];
        if (first === first.toUpperCase() && first !== first.toLowerCase()) count += 1;
    }
    return count >= 2;
}

// ---------------------------------------------------------------------------
// Реестр плейсхолдеров
// ---------------------------------------------------------------------------

class Registry {
    constructor() {
        this.byKey = new Map();     // канонический ключ → плейсхолдер
        this.entities = new Map();  // плейсхолдер → { text, type }
        this.counters = new Map();
    }

    /** Плейсхолдер для сущности; одна и та же сущность — один плейсхолдер. */
    resolve(text, type, normalized) {
        const key = type + ':' + normalized;
        const existing = this.byKey.get(key);
        if (existing) return existing;
        const n = (this.counters.get(type) || 0) + 1;
        this.counters.set(type, n);
        const placeholder = '[' + type + '_' + n + ']';
        this.byKey.set(key, placeholder);
        this.entities.set(placeholder, { text, type, normalized });
        return placeholder;
    }
}

// ---------------------------------------------------------------------------
// Движок
// ---------------------------------------------------------------------------

/** Склеивает пересекающиеся сущности; при разных типах остаётся более длинная. */
function mergeOverlapping(entities) {
    if (!entities.length) return [];
    const sorted = entities.slice().sort((a, b) => (a.start - b.start) || (b.end - a.end));
    const merged = [sorted[0]];
    for (let i = 1; i < sorted.length; i += 1) {
        const current = sorted[i];
        const last = merged[merged.length - 1];
        if (current.start < last.end) {
            if (current.end > last.end && current.type === last.type) last.end = current.end;
        } else {
            merged.push(current);
        }
    }
    return merged;
}

function overlapsAny(entities, s, e) {
    for (const ent of entities) {
        if (s < ent.end && ent.start < e) return true;
    }
    return false;
}

/** Начальные буквы слова вида «Е.В.», «Е.В», «Е.» — как на сервере. */
function initialLetters(word) {
    const out = [];
    // Цепочка одиночных заглавных букв через точку «Е.В.», «Е.В», «Е.» —
    // все буквы являются инициалами, в том числе последняя без точки.
    if (/^[А-ЯЁ](?:\.\s?[А-ЯЁ])*\.?$/.test(word)) {
        for (const ch of word) if (/[А-ЯЁ]/.test(ch)) out.push(ch.toLowerCase().replace(/ё/g, 'е'));
        return out;
    }
    const re = /[А-ЯЁа-яё](?=\.)/g;
    let m;
    while ((m = re.exec(word)) !== null) out.push(m[0].toLowerCase().replace(/ё/g, 'е'));
    return out;
}

/** Нормальная форма слова ФИО: самая длинная основа, распознанная как
 *  фамилия, имя или отчество. Если ни одна основа не распознана —
 *  всё слово целиком (опции[0]): для фамилий вне словаря pymorphy2
 *  основа и слово совпадают, и решать должна подпись целиком. */
function personStem(letters) {
    const options = stems(letters);
    let best = '';
    for (const st of options) {
        if (GIVEN_NAMES.has(st) || (st.length >= 6 && PATRONYMIC.test(st))
            || (st.length >= 4 && SURNAME.test(st))) {
            if (st.length > best.length) best = st;
        }
    }
    return best || options[0];
}

/**
 * Подпись лица: фамилия + отсортированные уникальные инициалы.
 * «Иванов Иван Иванович» и «Иванов И.И.» дают один плейсхолдер, иначе
 * один и тот же человек получил бы разные номера в документе.
 */
function personCanon(name) {
    const words = String(name).split(/[\s-]+/).filter(Boolean);
    let surname = null;
    const initials = [];

    for (const word of words) {
        const letters = word.replace(LETTERS_RE, '').toLowerCase().replace(/ё/g, 'е');
        if (!letters) continue;

        const inits = initialLetters(word);
        if (inits.length) {
            initials.push(...inits);
            continue;
        }

        const st = personStem(letters);
        if (!st) continue;
        if (surname === null) {
            if (GIVEN_NAMES.has(st) || (st.length >= 6 && PATRONYMIC.test(st))) {
                initials.push(st[0]);
                continue;
            }
            surname = st;
            continue;
        }
        if (PATRONYMIC.test(st) || GIVEN_NAMES.has(st)) initials.push(st[0]);
        else surname = st;
    }

    if (surname === null) {
        return words.map(w => {
            const letters = w.replace(LETTERS_RE, '').toLowerCase().replace(/ё/g, 'е');
            return letters ? personStem(letters) : w.toLowerCase();
        }).join(' ');
    }
    return surname + '|' + [...new Set(initials)].sort().join(' ');
}

function createEngine(options) {
    const opts = options || {};
    const companyName = (opts.companyName || '').trim();
    const extraOrgs = (opts.extraOrgs || []).filter(Boolean);
    const serverParity = opts.serverParity === true;
    const registry = opts.registry || new Registry();

    const companyRe = buildCompanyPattern(companyName);
    const extraOrgRes = extraOrgs.map(org => ({
        re: orgQueryPattern(org),
        canon: orgCanon(org),
    }));

    /** canon — готовая строка (канонический ключ) или функция от совпадения. */
    function pushMatches(list, text, re, type, canon) {
        re.lastIndex = 0;
        let m;
        while ((m = re.exec(text)) !== null) {
            const value = typeof canon === 'function'
                ? canon(m[0]) : (canon || m[0].toLowerCase());
            list.push({
                text: m[0], type, normalized: value,
                start: m.index, end: m.index + m[0].length,
            });
            if (m.index === re.lastIndex) re.lastIndex += 1;
        }
    }

    function extractRequisites(text) {
        const out = [];
        for (const item of REQUISITES) {
            item.re.lastIndex = 0;
            let m;
            while ((m = item.re.exec(text)) !== null) {
                    const gi = item.group || 0;
                const range = m.indices ? m.indices[gi] : null;
                const raw = range ? text.slice(range[0], range[1]) : m[0];
                if (range && item.check && !item.check(raw)) {
                    if (m.index === item.re.lastIndex) item.re.lastIndex += 1;
                    continue;
                }
                const start = range ? range[0] : m.index;
                // Обрезка применяется к маскируемому фрагменту: адрес берётся
                // до конца строки, но обрезается по границам предложения.
                const value = item.trim ? item.trim(raw) : raw;
                out.push({
                    text: value,
                    type: item.type,
                    normalized: item.canon ? item.canon(value) : value.toLowerCase(),
                    start,
                    end: start + value.length,
                });
                if (m.index === item.re.lastIndex) item.re.lastIndex += 1;
            }
        }
        return out;
    }

    function extractPersons(text) {
        const out = [];

        // ФИО словами: «Иванов Иван Иванович», «Иванов Иванович».
        const words = [];
        const wordRe = new RegExp(
            LB + '(?:[А-ЯЁ][а-яё]+|[А-ЯЁ]{2,})' + RB, 'g');
        let m;
        while ((m = wordRe.exec(text)) !== null) {
            words.push({ text: m[0], start: m.index, end: m.index + m[0].length });
        }
        for (let i = 0; i < words.length; i += 1) {
            for (const size of [3, 2]) {
                if (i + size > words.length) continue;
                const group = words.slice(i, i + size);
                let joined = true;
                for (let k = 1; k < group.length; k += 1) {
                    if (!/^\s+$/.test(text.slice(group[k - 1].end, group[k].start))) {
                        joined = false;
                        break;
                    }
                }
                if (!joined) continue;
                const candidate = group.map(w => w.text).join(' ');
                if (!isLikelyPersonName(candidate)) continue;
                out.push({
                    text: candidate,
                    type: PERSON,
                    normalized: personCanon(candidate),
                    start: group[0].start,
                    end: group[size - 1].end,
                });
                break;
            }
        }

        // Инициалы: «И.О. Иванов», «Иванов И.И.».
        INITIALS_SPAN.lastIndex = 0;
        while ((m = INITIALS_SPAN.exec(text)) !== null) {
            const trimmed = trimPersonTail(text, m.index, m.index + m[0].length);
            if (trimmed.end - trimmed.start < 2) continue;
            const nameText = text.slice(trimmed.start, trimmed.end);
            // «И.О. Фамилия» / «М.П. Приложение» — бланк, а не человек.
            const lettersRe = /[А-ЯЁа-яё]+/g;
            let hasName = false;
            let lm;
            while ((lm = lettersRe.exec(nameText)) !== null) {
                if (isPersonWord(lm[0])) { hasName = true; break; }
            }
            // Фамилии вне словаря («А.И. Козяйчев») инициалы всё же выдают:
            // после «А.И.» стоит фамилия, а не слово из договора.
            if (!hasName && initialsAnchorAccepts(nameText)) hasName = true;
            if (!hasName) continue;
            out.push({
                text: nameText,
                type: PERSON,
                normalized: personCanon(nameText),
                start: trimmed.start,
                end: trimmed.end,
            });
        }

        // Одиночная фамилия или имя: «Иванов», «Иванову», «Ивану».
        // Проверка строже, чем у составных ФИО: прилагательные вроде
        // «торговый» и «ленинградский» персоной не считаются.
        for (const w of words) {
            if (isStreetNameContext(text, w.start)) continue;
            if (!isPersonWord(w.text)) continue;
            if (ADJECTIVE_ENDING.test(w.text.toLowerCase().replace(/ё/g, 'е'))) continue;
            if (isLikelyPersonName(text.slice(w.start, w.end))) continue;
            const trimmed = trimPersonTail(text, w.start, w.end);
            if (trimmed.end - trimmed.start < 2) continue;
            out.push({
                text: text.slice(trimmed.start, trimmed.end),
                type: PERSON,
                normalized: personCanon(w.text),
                start: trimmed.start,
                end: trimmed.end,
            });
        }

        return out;
    }

    function extractOrgs(text, existing) {
        const out = [];

        pushMatches(out, text, companyRe, ORG, DID_ORG_CANON);
        pushMatches(out, text, ROSUIM, ORG, ROSUIM_CANON);
        for (const item of extraOrgRes) pushMatches(out, text, item.re, ORG, item.canon);

        // Контрагент по обороту «…, именуемое в дальнейшем "Роль"».
        ORG_DECL.lastIndex = 0;
        let m;
        while ((m = ORG_DECL.exec(text)) !== null) {
            const name = m[1].trim().replace(/^[ \t,;()]+|[ \t,;()]+$/g, '');
            if (name.length < 4) continue;
            if (ORG_DECL_REJECT.test(name)) continue;
            if (reTest(companyRe, name) || reTest(ROSUIM, name)) continue;
            if (!looksLikeOrgName(name)) continue;
            const type = isLikelyPersonName(name) ? PERSON : ORG;
            const normalized = orgCanon(name);
            const start = m.index + m[0].indexOf(m[1]);
            out.push({ text: name, type, normalized, start, end: start + name.length });

            // Сокращение в скобках: «(Музей политической истории России)».
            const parenRe = /\(([^()]{4,})\)/g;
            let pm;
            while ((pm = parenRe.exec(m[1])) !== null) {
                if (looksLikeOrgAlias(pm[1])) {
                    pushMatches(out, text, orgQueryPattern(pm[1]), type, normalized);
                }
            }
        }

        // «ООО "ДЕЗ"», «ООО Ромашка», «ФГБУ ДОД» и полные формы ОПФ дают
        // один плейсхолдер; последующие упоминания получают то же значение.
        //
        // Сначала собираем сами упоминания и разбираем перекрытия между ними,
        // и только затем расставляем псевдонимы. Иначе псевдоним «ДОД», найденный
        // внутри «Федеральное … учреждение «ДОД»», занял бы это место, и
        // развёрнутое упоминание осталось бы с ОПФ и кавычками.
        const primary = [];
        const aliases = [];
        const orgMatches = [];
        for (const re of [ORG_FORM_QUOTED, ORG_FORM_BARE]) {
            re.lastIndex = 0;
            let m;
            while ((m = re.exec(text)) !== null) {
                orgMatches.push({ m: m, bare: m.groups.open === undefined });
            }
        }
        orgMatches.sort((a, b) => a.m.index - b.m.index);

        for (const { m: lm, bare } of orgMatches) {
            const g = lm.groups;
            const raw = g.name;
            const name = bare ? trimBareOrgName(raw) : raw.trim();
            if (!name || name.length < 2) continue;
            // Наименование — последняя часть совпадения: в кавычках оно стоит
            // перед закрывающей кавычкой, без кавычек — в самом конце.
            const nameStart = lm.index + lm[0].lastIndexOf(raw);
            const nameEnd = nameStart + name.length;
            // В кавычках совпадение заканчивается закрывающей кавычкой, без
            // кавычек — обрезанным наименованием.
            const start = lm.index;
            const end = bare ? nameEnd : lm.index + lm[0].length;
            // ФГУП «ДИД», Росимущество и контрагент извлечены выше: перекрытие
            // пропускаем, чтобы не плодить второй плейсхолдер.
            if (overlapsAny(out, start, end) || overlapsAny(existing, start, end)) continue;
            if (overlapsAny(primary, start, end)) continue;
            // Ссылка на организацию маскируется целиком вместе с ОПФ, даже если
            // наименование совпадает с фамилией: «АО Иванов» → [ORG_1].
            const normalized = orgCanon(name);
            const span = { text: text.slice(start, end), type: ORG, normalized, start, end };
            out.push(span);
            primary.push(span);
            // Сокращение сразу в скобках: АО «Полное» («Сбербанк-АСТ»).
            const tail = PAREN_ALIAS.exec(text.slice(end));
            aliases.push({
                alias: bare ? name : text.slice(lm.index + lm[0].lastIndexOf(g.open), end),
                key: normalized,
                at: bare ? nameStart : lm.index + lm[0].lastIndexOf(g.open),
            });
            if (tail && orgCanon(tail[1]) !== normalized) {
                aliases.push({ alias: tail[1], key: normalized, at: -1 });
            }
        }

        // Псевдонимы не должны залезать в уже занятое упоминание.
        const taken = new Set();
        for (const { alias, key, at } of aliases) {
            if (!alias || alias.length < 3) continue;
            const re = new RegExp(LB + escapeRegExp(alias) + RB, 'g');
            let am;
            while ((am = re.exec(text)) !== null) {
                if (am.index === at) continue;
                const s = am.index;
                const e = s + alias.length;
                if (overlapsAny(primary, s, e) || overlapsAny(existing, s, e)) continue;
                const sig = s + ':' + key;
                if (taken.has(sig)) continue;
                taken.add(sig);
                out.push({ text: alias, type: ORG, normalized: key, start: s, end: e });
            }
        }

        return out;
    }

    function analyze(text) {
        const source = String(text == null ? '' : text);
        if (!source.trim()) return [];
        let requisites = extractRequisites(source);
        if (serverParity) {
            requisites = requisites.filter((entity) => {
                // Серверный RegexPatterns относит 13-значный ОГРН к INN.
                if (entity.type === 'OGRN') {
                    entity.type = 'INN';
                    entity.normalized = entity.text.toLowerCase();
                    return true;
                }
                return SERVER_PARITY_TYPES.has(entity.type);
            });
        }
        // Ведущая пунктуация не является частью адреса: «.fgup-info@…» и
        // «fgup-info@…» должны получать один placeholder, как на сервере.
        for (const entity of requisites) {
            if (entity.type !== 'EMAIL') continue;
            entity.normalized = entity.normalized.replace(/^[.]+/, '');
        }
        const persons = extractPersons(source);
        const known = requisites.concat(persons);
        let orgs = extractOrgs(source, known);
        if (serverParity) {
            orgs = orgs.filter(entity => !SERVER_PARITY_ORG_REJECT.has(entity.normalized));
        }
        return mergeOverlapping(known.concat(orgs));
    }

    /** Сопоставляет сущностям плейсхолдеры общего реестра. */
    function assign(entities) {
        return entities.map(e => ({
            start: e.start,
            end: e.end,
            text: registry.resolve(e.text, e.type, e.normalized),
            type: e.type,
        }));
    }

    function applySpans(text, spans) {
        if (!spans.length) return text;
        let out = '';
        let cursor = 0;
        for (const s of spans) {
            out += text.slice(cursor, s.start) + s.text;
            cursor = s.end;
        }
        return out + text.slice(cursor);
    }

    function report() {
        const byType = {};
        const mapping = {};
        for (const [placeholder, ent] of registry.entities) {
            byType[ent.type] = (byType[ent.type] || 0) + 1;
            mapping[placeholder] = serverParity && ent.type === ORG
                && ent.normalized === DID_ORG_CANON
                ? SERVER_PARITY_DID_DISPLAY : ent.text;
        }
        return { entities_found: registry.entities.size, entities_by_type: byType, mapping };
    }

    return { analyze, assign, applySpans, report, registry };
}

const API = {
    createEngine, Registry, TYPE_LABELS, TYPE_ORDER,
    orgCanon, personCanon, phoneCanon, passportCanon, personalNumCanon,
};

if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (global) global.AnonymizeCore = API;

})(typeof window !== 'undefined' ? window : null);
