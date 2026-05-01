import json
import unittest
from pathlib import Path

from src.skills.registry import SkillRegistry


class DesignKnowledgeResourceTests(unittest.TestCase):
    def _resource_root(self) -> Path:
        return Path(__file__).resolve().parents[1] / "skills" / "design-knowledge-and-skills"

    def test_design_knowledge_skill_is_discovered(self) -> None:
        registry = SkillRegistry(workspace=Path("/tmp/nonexistent-creative-claw-workspace"))
        skills = {item.name: item for item in registry.list_skills()}

        self.assertIn("design-knowledge-and-skills", skills)
        self.assertIn("Index and retrieval guide", skills["design-knowledge-and-skills"].description)

    def test_resource_manifest_indexes_core_design_resources(self) -> None:
        manifest = json.loads((self._resource_root() / "resource-manifest.json").read_text(encoding="utf-8"))
        resources = manifest["resources"]
        resource_ids = {resource["id"] for resource in resources}

        self.assertEqual(manifest["selectionPolicy"]["briefElementsFirst"], True)
        self.assertIn("schema.design_brief_v1", resource_ids)
        self.assertIn("schema.design_product_result_v1", resource_ids)
        self.assertIn("brief_elements.dashboard", resource_ids)
        self.assertIn("brief_elements.landing_page", resource_ids)
        self.assertIn("brief_elements.mobile_app", resource_ids)
        self.assertIn("brief_elements.deck", resource_ids)
        self.assertIn("brief_elements.operation_data_ui", resource_ids)
        self.assertIn("brief_elements.admin_console", resource_ids)
        self.assertIn("brief_elements.marketing_campaign_page", resource_ids)
        self.assertIn("brief_elements.social_carousel", resource_ids)
        self.assertIn("brief_elements.html_deck", resource_ids)
        self.assertIn("brief_elements.pricing_page", resource_ids)
        self.assertIn("brief_elements.docs_page", resource_ids)
        self.assertIn("brief_elements.kanban_board", resource_ids)
        self.assertIn("brief_elements.magazine_poster", resource_ids)
        self.assertIn("brief_elements.wireframe_sketch", resource_ids)
        self.assertIn("task_skill.dashboard", resource_ids)
        self.assertIn("task_skill.saas-landing", resource_ids)
        self.assertIn("task_skill.social-carousel", resource_ids)
        self.assertIn("design_system.linear-app", resource_ids)
        self.assertIn("device_frame.iphone-15-pro", resource_ids)
        self.assertEqual(
            sum(1 for resource in resources if resource["type"] == "contract_schema"),
            2,
        )
        self.assertEqual(
            sum(1 for resource in resources if resource["type"] == "brief_element_schema"),
            14,
        )

    def test_contract_schemas_define_stable_versions(self) -> None:
        schema_dir = self._resource_root() / "schemas"
        design_brief_schema = json.loads((schema_dir / "design-brief-v1.schema.json").read_text(encoding="utf-8"))
        result_schema = json.loads((schema_dir / "design-product-result-v1.schema.json").read_text(encoding="utf-8"))

        self.assertEqual(design_brief_schema["properties"]["schema_version"]["const"], "design-brief-v1")
        self.assertIn("design_system", design_brief_schema["required"])
        self.assertIn("constraints", design_brief_schema["required"])
        self.assertEqual(
            result_schema["properties"]["result_schema_version"]["const"],
            "design-product-result-v1",
        )
        self.assertIn("design_validation", result_schema["required"])

    def test_brief_elements_provide_question_templates(self) -> None:
        brief_elements = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((self._resource_root() / "brief-elements").glob("*.json"))
        ]

        self.assertTrue(brief_elements)
        for brief_element in brief_elements:
            self.assertEqual(brief_element["type"], "brief_element_schema")
            self.assertTrue(brief_element["required_fields"], brief_element["id"])
            self.assertTrue(brief_element["question_templates"], brief_element["id"])
            self.assertIn("recommended_skill", brief_element["defaults"])

    def test_design_resource_files_do_not_embed_local_absolute_paths(self) -> None:
        for path in [
            self._resource_root() / "SKILL.md",
            self._resource_root() / "resource-manifest.json",
            self._resource_root() / "resource-index.md",
            *sorted((self._resource_root() / "schemas").glob("*.json")),
            *sorted((self._resource_root() / "brief-elements").glob("*.json")),
        ]:
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("/" + "Users/", content, path.as_posix())


if __name__ == "__main__":
    unittest.main()
