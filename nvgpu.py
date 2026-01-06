from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

from dbgpu import GPUDatabase, GPUSpecification


@dataclass(frozen=True)
class NvidiaGpuRow:
	name: str
	memory_capacity: str
	memory_type: str

	def to_line(self) -> str:
		return f"\"{self.name}\" \"{self.memory_capacity} {self.memory_type}\""


def _format_memory_capacity(memory_size_gb: Optional[float]) -> str:
	if memory_size_gb is None:
		return "不明"

	if memory_size_gb <= 0:
		return "0GB"

	rounded = round(memory_size_gb)
	if abs(memory_size_gb - rounded) < 1e-9:
		return f"{int(rounded)}GB"

	s = f"{memory_size_gb:.3f}".rstrip("0").rstrip(".")
	return f"{s}GB"


def _row_from_spec(spec: GPUSpecification) -> NvidiaGpuRow:
	name = (spec.name or "").strip()
	mem_capacity = _format_memory_capacity(spec.memory_size_gb)
	mem_type = (spec.memory_type or "不明").strip()
	return NvidiaGpuRow(name=name, memory_capacity=mem_capacity, memory_type=mem_type)


def _is_console_gpu_name(name: str) -> bool:
	n = name.casefold()
	# コンソール/携帯ゲーム機向けGPUとして扱うものを除外
	# - PlayStation系 (dbgpu側の表記ゆれを吸収)
	# - Xbox系
	# - Nintendo Switch系
	# - Tegra GPU (Switch/組み込み向け系統として除外)
	console_markers = (
		"playstation",
		"psx",
		"xbox",
		"switch",
		"nintendo",
		"tegra",
	)
	return any(m in n for m in console_markers)


def iter_nvidia_gpu_rows(specs: Iterable[GPUSpecification]) -> Iterable[NvidiaGpuRow]:
	for spec in specs:
		if spec.manufacturer != "NVIDIA":
			continue
		row = _row_from_spec(spec)
		if not row.name:
			continue
		if _is_console_gpu_name(row.name):
			continue
		yield row


def build_unique_sorted_rows(rows: Iterable[NvidiaGpuRow]) -> list[NvidiaGpuRow]:
	unique: dict[Tuple[str, str, str], NvidiaGpuRow] = {}
	for r in rows:
		key = (r.name, r.memory_capacity, r.memory_type)
		unique.setdefault(key, r)

	def sort_key(r: NvidiaGpuRow) -> Tuple[str, str, str]:
		return (r.name.lower(), r.memory_capacity, r.memory_type)

	return sorted(unique.values(), key=sort_key)


def write_rows(out_path: Path, rows: Iterable[NvidiaGpuRow]) -> int:
	out_path.parent.mkdir(parents=True, exist_ok=True)
	lines = [r.to_line() for r in rows]
	out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
	return len(lines)


def main() -> int:
	db = GPUDatabase.default()
	rows = build_unique_sorted_rows(iter_nvidia_gpu_rows(db.specs))
	out_path = Path(__file__).with_name("ALL_NVIDIA_GPU.txt")
	count = write_rows(out_path, rows)
	print(f"Wrote {count} NVIDIA GPUs to: {out_path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
