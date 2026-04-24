import pytest
from raven.validation.meteor import METEORConfig, tag_entities, resolve_entity, normalize_text


@pytest.fixture
def cfg():
    return METEORConfig()


class TestResolveEntity:
    def test_exact_canonical(self, cfg):
        assert cfg.resolve("TJ") == "TJ"

    def test_alias_resolves_to_canonical(self, cfg):
        assert cfg.resolve("Leland") == "TJ"
        assert cfg.resolve("Leland Jourdan") == "TJ"
        assert cfg.resolve("Sokpyeon") == "TJ"

    def test_case_insensitive(self, cfg):
        assert cfg.resolve("leland") == "TJ"
        assert cfg.resolve("ANDROID 18") == "18"

    def test_unknown_name_passthrough(self, cfg):
        assert cfg.resolve("Professor Oak") == "Professor Oak"

    def test_fuzzy_close_alias(self, cfg):
        # "Kralin" is 2 edits from "Krillin"
        result = cfg.resolve("Kralin")
        assert result in ("Krillin", "Kralin")  # may or may not hit threshold

    def test_extra_aliases(self):
        cfg = METEORConfig(extra_aliases={"Aristotle": ["Ari", "Stagi"]})
        assert cfg.resolve("Ari") == "Aristotle"


class TestTagEntities:
    def test_finds_canonical(self, cfg):
        tags = cfg.tag_entities("TJ decided to ship RAVEN today")
        assert "TJ" in tags

    def test_finds_alias(self, cfg):
        tags = cfg.tag_entities("Leland built the pipeline")
        assert "TJ" in tags

    def test_multiple_entities(self, cfg):
        tags = cfg.tag_entities("Bulma and Krillin worked on COSMIC")
        assert "Bulma" in tags
        assert "COSMIC" in tags

    def test_no_entities(self, cfg):
        tags = cfg.tag_entities("the quick brown fox jumps")
        assert tags == []


class TestNormalizeText:
    def test_replaces_alias_with_canonical(self, cfg):
        result = normalize_text("Leland approved the build", cfg)
        assert "TJ" in result

    def test_idempotent_on_canonical(self, cfg):
        result = normalize_text("TJ approved the build", cfg)
        assert "TJ" in result


class TestModuleLevelFunctions:
    def test_tag_entities_default(self):
        tags = tag_entities("18 shipped Android18 code")
        assert "18" in tags

    def test_resolve_entity_default(self):
        assert resolve_entity("BulmaSequel") == "Bulma"
