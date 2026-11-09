"""The interface a metrics formatter implements.

A formatter turns the collectors' raw results (collect_all_metrics's
"dimensions") into a presentation of them. The dashboard's per-section
HTML (formatters/dashboard/) is the one implementation so far.
"""

from abc import ABC, abstractmethod
from typing import Dict


class Formatter(ABC):
    @abstractmethod
    def format(self, metrics: Dict, overrides: Dict[str, Dict[str, str]]) -> Dict:
        """Format one package's metrics.

        Args:
            metrics: collect_all_metrics's result for the package.
            overrides: the package config's `overrides:` block --
                section number -> {sub-metric label: value to show}.
        """
