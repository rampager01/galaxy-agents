"""Allow running with python -m src."""
from src.runner import main
import asyncio

asyncio.run(main())
