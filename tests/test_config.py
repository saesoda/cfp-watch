from pathlib import Path

from cfpwatch.config import load_journals

ROOT = Path(__file__).resolve().parents[1]


def test_load_journals_reads_all_twelve():
    journals = load_journals(ROOT / "config" / "journals.yaml")
    keys = [j.key for j in journals]
    assert keys == ["asq", "smj", "sej", "os", "amj", "ms", "jom", "jms", "stsc", "oss", "jibs", "rp"]
    assert len(set(keys)) == 12
    smj = next(j for j in journals if j.key == "smj")
    assert smj.name == "Strategic Management Journal"
    assert "SMJ" in smj.aliases
    assert isinstance(smj.direct_urls, list)
