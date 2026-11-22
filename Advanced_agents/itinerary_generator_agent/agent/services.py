from dataclasses import dataclass

from agent.llm import StructuredLLM
from agent.memory import QdrantMemory
from agent.tools import TravelTools


@dataclass
class Services:
    tools: TravelTools
    llm: StructuredLLM
    memory: QdrantMemory
    max_replans: int = 3

    def close(self) -> None:
        self.tools.close()
        self.memory.close()
