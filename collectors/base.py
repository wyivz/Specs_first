from __future__ import annotations

from abc import ABC, abstractmethod

from schemas import EvidenceItem, OfficialSpec, PriceFinding, ProductCandidate


class Collector(ABC):
    @abstractmethod
    def discover_candidates(self, query: str, category: str) -> list[ProductCandidate]:
        raise NotImplementedError

    @abstractmethod
    def collect_official_specs(self, candidate: ProductCandidate) -> tuple[list[OfficialSpec], list[str]]:
        raise NotImplementedError

    @abstractmethod
    def collect_real_world_corpus(self, candidate: ProductCandidate) -> list[EvidenceItem]:
        raise NotImplementedError

    @abstractmethod
    def collect_prices(self, candidate: ProductCandidate) -> list[PriceFinding]:
        raise NotImplementedError
