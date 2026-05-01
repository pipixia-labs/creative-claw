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
        self.assertIn("brief_elements.dashboard", resource_ids)
        self.assertIn("brief_elements.landing_page", resource_ids)
        self.assertIn("brief_elements.mobile_app", resource_ids)
        self.assertIn("brief_elements.deck", resource_ids)
        self.assertIn("task_skill.dashboard", resource_ids)
        self.assertIn("task_skill.saas-landing", resource_ids)
        self.assertIn("design_system.linear-app", resource_ids)
        self.assertIn("device_frame.iphone-15-pro", resource_ids)

    def test_brief_elements_provide_question_templates(self) -> None:
        dashboard = json.loads(
            (self._resource_root() / "brief-elements" / "dashboard.json").read_text(encoding="utf-8")
        )

        self.assertEqual(dashboard["type"], "brief_element_schema")
        self.assertIn("metrics", dashboard["required_fields"])
        self.assertTrue(dashboard["question_templates"])
        self.assertEqual(dashboard["defaults"]["recommended_skill"], "dashboard")

    def test_design_resource_files_do_not_embed_local_absolute_paths(self) -> None:
        for path in [
            self._resource_root() / "SKILL.md",
            self._resource_root() / "resource-manifest.json",
            self._resource_root() / "resource-index.md",
            *sorted((self._resource_root() / "brief-elements").glob("*.json")),
        ]:
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("/" + "Users/", content, path.as_posix())


if __name__ == "__main__":
    unittest.main()
