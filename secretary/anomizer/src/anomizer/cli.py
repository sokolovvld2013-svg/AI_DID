from __future__ import annotations

import tempfile
from pathlib import Path

import click

from .converter import ensure_docx, has_libreoffice
from .pipeline import MaskingReport, anonymize_docx


def print_table(headers, rows):
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    
    header_line = " | ".join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print(" | ".join(str(cell).ljust(w) for cell, w in zip(row, col_widths)))


def get_output_path(input_path: Path, output_dir: Path | None = None) -> Path:
    if output_dir is None:
        output_dir = Path.cwd() / "newdoc"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    stem = input_path.stem
    suffix = ".docx"  # Always save as .docx
    return output_dir / f"{stem}_изм{suffix}"


@click.command()
@click.argument('input_file', type=click.Path(exists=True, path_type=Path))
@click.argument('output_file', type=click.Path(path_type=Path), required=False)
@click.option('--report', '-r', type=click.Path(path_type=Path), help='Path to save masking report (JSON)')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
def main(input_file: Path, output_file: Path | None, report: Path | None, verbose: bool):
    """
    Анонимизация DOCX/DOC файлов перед отправкой в LLM.
    
    Заменяет чувствительные данные на плейсхолдеры:
    - ФИО -> [PERSON_1], [PERSON_2]...
    - Наименование компании -> [ORG_1], [ORG_2]...
    - ИНН -> [INN_1], [INN_2]...
    - Паспорт -> [PASSPORT_1], [PASSPORT_2]...
    - Персональный номер -> [PERSNUM_1], [PERSNUM_2]...
    - Email -> [EMAIL_1], [EMAIL_2]...
    - Телефоны -> [PHONE_1], [PHONE_2]...
    
    Гарантирует консистентность: одно и то же лицо везде получает один плейсхолдер.
    
    Поддерживает .doc (через LibreOffice) и .docx.
    По умолчанию сохраняет в ./newdoc/ с суффиксом _изм
    """
    if output_file is None:
        output_file = get_output_path(input_file)
    
    if verbose:
        print(f"Обработка: {input_file} -> {output_file}")
    
    # Convert .doc to .docx if needed
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = ensure_docx(input_file, Path(tmpdir))
        if not docx_path:
            print("Ошибка: не удалось подготовить .docx файл")
            raise click.Abort()
        
        try:
            print("Анонимизация...")
            report_obj = anonymize_docx(str(docx_path), str(output_file), str(report) if report else None)
            
            print("\nГотово!")
            print(f"Найдено сущностей: {report_obj.entities_found}")
            print(f"Сохранено в: {output_file}")
            
            if verbose and report_obj.entities_found > 0:
                print("\nТипы сущностей:")
                rows = [(k, v) for k, v in sorted(report_obj.entities_by_type.items())]
                print_table(["Тип", "Количество"], rows)
                
                if report:
                    print(f"\nОтчет сохранен в: {report}")
            
            if verbose and report_obj.mapping:
                print("\nМаппинг замен:")
                rows = [(k, v) for k, v in sorted(report_obj.mapping.items())]
                print_table(["Плейсхолдер", "Оригинал"], rows)
        
        except Exception as e:
            print(f"Ошибка: {e}")
            raise click.Abort()


if __name__ == '__main__':
    main()