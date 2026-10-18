import json
from pathlib import Path

from engine_header_evidence import _lexical_source, _owner_region


def test_owner_regions_share_javascript_fixtures():
    fixtures = json.loads((Path(__file__).parent / "fixtures/lexical-owner-regions.json").read_text())
    for item in fixtures:
        region = _owner_region(_lexical_source(item["source"]), item["owner"])
        assert bool(region) == item["found"], item["source"]
        if region:
            assert item["source"][region[1]] == "{"
            assert item["source"][region[2] - 1] == "}"
