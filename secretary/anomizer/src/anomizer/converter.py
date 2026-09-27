from __future__ import annotations
import subprocess
import shutil
from pathlib import Path
from typing import Optional


def has_libreoffice() -> bool:
    return shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


def get_libreoffice_cmd() -> Optional[str]:
    for cmd in ["soffice", "libreoffice"]:
        if shutil.which(cmd):
            return cmd
    return None


def convert_doc_to_docx(input_path: Path, output_dir: Path) -> Optional[Path]:
    """
    Convert .doc to .docx using LibreOffice headless.
    Returns path to converted .docx file or None on failure.
    """
    cmd = get_libreoffice_cmd()
    if not cmd:
        return None
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        result = subprocess.run(
            [
                cmd,
                "--headless",
                "--convert-to", "docx",
                "--outdir", str(output_dir),
                str(input_path)
            ],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode != 0:
            print(f"LibreOffice error: {result.stderr}")
            return None
        
        # Find the generated .docx file
        output_file = output_dir / f"{input_path.stem}.docx"
        if output_file.exists():
            return output_file
        
        # Sometimes LibreOffice creates with different casing
        for f in output_dir.glob(f"{input_path.stem}*.docx"):
            return f
            
        return None
        
    except subprocess.TimeoutExpired:
        print("LibreOffice conversion timeout")
        return None
    except Exception as e:
        print(f"Conversion error: {e}")
        return None


def ensure_docx(input_path: Path, work_dir: Path) -> Optional[Path]:
    """
    Ensure we have a .docx file. If input is .doc, convert it.
    Returns path to .docx file.
    """
    if input_path.suffix.lower() == ".docx":
        return input_path
    
    if input_path.suffix.lower() == ".doc":
        if not has_libreoffice():
            print("Ошибка: LibreOffice не установлен. Установите для конвертации .doc файлов.")
            return None
        
        print(f"Конвертация {input_path.name} в .docx...")
        docx_path = convert_doc_to_docx(input_path, work_dir)
        if docx_path:
            print(f"Конвертировано: {docx_path.name}")
            return docx_path
        return None
    
    print(f"Неподдерживаемый формат: {input_path.suffix}")
    return None