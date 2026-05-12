import unittest

from src.productions.design.design_product_manager import ProductDesignSkillRegistry


class ProductDesignSkillsRootTests(unittest.TestCase):
    def test_private_product_design_skills_are_the_runtime_calibration_surface(self) -> None:
        registry = ProductDesignSkillRegistry()

        skills = registry.list_skills()

        skill_names = {skill.name for skill in skills}
        self.assertGreaterEqual(len(skills), 1)
        self.assertIn("design-canvas-artifact", skill_names)
        self.assertNotIn("poster-page-designer", skill_names)


if __name__ == "__main__":
    unittest.main()
