"""mailmerge — send personalised emails from a CSV file and a template."""

from .core import (
    MergeError,
    MessageTemplate,
    build_message,
    check_rows,
    load_template,
    parse_database,
    read_database,
    render_message,
)
from .attachments import assign_files, check_size, expand_uploads, pick_files
from .letters import (LetterSet, build_letters, fit_image, image_role, images_needed,
                      load_library, missing_images, odd_letters)
from .sender import Sender, ServerConfig

__all__ = [
    "MergeError",
    "MessageTemplate",
    "build_message",
    "check_rows",
    "load_template",
    "parse_database",
    "read_database",
    "render_message",
    "expand_uploads",
    "pick_files",
    "assign_files",
    "build_letters",
    "load_library",
    "images_needed",
    "missing_images",
    "odd_letters",
    "fit_image",
    "image_role",
    "LetterSet",
    "check_size",
    "ServerConfig",
    "Sender",
]
__version__ = "0.9.1"
