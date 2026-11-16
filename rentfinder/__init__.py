"""rentfinder: on-demand rental search for east Riyadh (bayut.sa)."""
import logging

__version__ = "1.0.0"

# Library-style logging: the CLI (__main__) attaches the real handlers.
logging.getLogger("rentfinder").addHandler(logging.NullHandler())
