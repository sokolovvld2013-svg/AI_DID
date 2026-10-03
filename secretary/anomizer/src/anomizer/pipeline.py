from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .docx_processor import DocxProcessor, process_docx
from .ner import ExtractedEntity, NERExtractor
from .registry import Entity, EntityRegistry


@dataclass
class MaskingReport:
    input_file: str
    output_file: str
    entities_found: int
    entities_by_type: Dict[str, int]
    mapping: Dict[str, str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def save(self, path: str):
        Path(path).write_text(self.to_json(), encoding='utf-8')


class AnomizerPipeline:
    def __init__(self):
        self.processor = DocxProcessor()

    def process(self, input_path: str, output_path: str, report_path: Optional[str] = None,
                extra_orgs: Optional[List[str]] = None) -> MaskingReport:
        mapping = self.processor.process(input_path, output_path, extra_orgs)
        
        entities_by_type: Dict[str, int] = {}
        for entity in mapping.values():
            entities_by_type[entity.entity_type] = entities_by_type.get(entity.entity_type, 0) + 1
        
        placeholder_mapping = {placeholder: entity.text for placeholder, entity in mapping.items()}
        
        report = MaskingReport(
            input_file=input_path,
            output_file=output_path,
            entities_found=len(mapping),
            entities_by_type=entities_by_type,
            mapping=placeholder_mapping
        )
        
        if report_path:
            report.save(report_path)
        
        return report


def anonymize_docx(input_path: str, output_path: str, report_path: Optional[str] = None,
                   extra_orgs: Optional[List[str]] = None) -> MaskingReport:
    pipeline = AnomizerPipeline()
    return pipeline.process(input_path, output_path, report_path, extra_orgs)