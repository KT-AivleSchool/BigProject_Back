import os
from typing import Any, Dict
from jinja2 import Environment, FileSystemLoader, ChoiceLoader, DictLoader

# Define base paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
WORKSPACE_TEMPLATES_DIR = os.path.join(os.path.dirname(BASE_DIR), "templates")

# Ensure template directories exist
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# Build loaders list in order of priority
loaders = [
    FileSystemLoader(TEMPLATES_DIR),
    FileSystemLoader(WORKSPACE_TEMPLATES_DIR),
    DictLoader({}),  # Fallback empty loader
]

# Initialize global Jinja2 Environment
jinja2_env = Environment(
    loader=ChoiceLoader(loaders),
    autoescape=False,  # Set to False by default for AI prompts to avoid escaping HTML characters like < and >
    trim_blocks=True,
    lstrip_blocks=True,
)


# Define and register custom utility filters if needed
def json_filter(value: Any) -> str:
    """Helper filter to format JSON within templates."""
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)


jinja2_env.filters["json"] = json_filter


def render_template(template_name: str, context: Dict[str, Any] = None) -> str:
    """
    Renders a template file from the configured template directories.
    """
    if context is None:
        context = {}
    template = jinja2_env.get_template(template_name)
    return template.render(**context)


def render_template_string(template_str: str, **context: Any) -> str:
    """
    Renders a template string directly using the Jinja2 environment.
    This is especially useful for rendering dynamically constructed prompts.
    """
    template = jinja2_env.from_string(template_str)
    return template.render(**context)
