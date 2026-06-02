# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lawyer.citations import parse_cited_fragment_ids, select_citations_for_display

assert parse_cited_fragment_ids("См. [1] и [3].") == {1, 3}

cits = [
    {"id": 1, "text": "a"},
    {"id": 2, "text": "b"},
    {"id": 3, "text": "c"},
]
out = select_citations_for_display("Ответ [2].", cits)
assert len(out) == 1 and out[0]["id"] == 2
print("OK citations filter")
