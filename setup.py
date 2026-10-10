"""Build immutable runtime resources into the package without editing sources."""
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


PROJECT_ROOT = Path(__file__).resolve().parent


def resource_sources():
    descriptors = sorted((PROJECT_ROOT / "capabilities").glob("*.json"))
    agent = PROJECT_ROOT / "agents" / "research_workbench.toml"
    if not descriptors or not agent.is_file():
        raise RuntimeError("Capability descriptors and the Agent definition are required build inputs")
    return descriptors + [agent]


class BuildRuntimeResources(build_py):
    """Keep source, wheel and editable layouts explicit and independently usable."""

    def get_source_files(self):
        return super().get_source_files() + [
            str(path.relative_to(PROJECT_ROOT)) for path in resource_sources()
        ]

    def get_output_mapping(self):
        mapping = super().get_output_mapping()
        for source in resource_sources():
            destination = Path(self.build_lib) / "research_workbench" / "resources" / source.relative_to(PROJECT_ROOT)
            mapping[str(destination)] = str(source.relative_to(PROJECT_ROOT))
        return mapping

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        for destination in self.get_output_mapping():
            if destination not in outputs:
                outputs.append(destination)
        return outputs

    def run(self):
        # An editable install uses the intact source-layout resources directly.
        if self.editable_mode:
            return
        super().run()
        for source in resource_sources():
            destination = Path(self.build_lib) / "research_workbench" / "resources" / source.relative_to(PROJECT_ROOT)
            self.mkpath(str(destination.parent))
            self.copy_file(str(source), str(destination))


setup(cmdclass={"build_py": BuildRuntimeResources})
