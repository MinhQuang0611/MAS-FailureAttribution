"""
main.py — Entry point cho T2SQL Failure Attribution Pipeline

Usage:
    python main.py --benchmark data/mini_benchmark.json --model gpt-4o --limit 10
    python main.py --benchmark data/mini_benchmark.json --model gpt-4o --all
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

# ---------------------------------------------------------------------------
# Setup paths: thêm thư mục gốc vào sys.path để import các module local
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

from evaluation.root_cause import RootCauseClassifier
from pipeline.orchestrator import run_pipeline_for_sample

console = Console()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option(
    "--benchmark",
    default=None,
    help="Đường dẫn đến file benchmark JSON (nếu chạy 1 file).",
)
@click.option(
    "--benchmark-dir",
    default=None,
    help="Thư mục chứa các file benchmark JSON để chạy tuần tự.",
)
@click.option(
    "--model",
    default="gpt-4o",
    show_default=True,
    help="Tên model LLM sẽ dùng.",
)
@click.option(
    "--db-dir",
    default="data/databases",
    show_default=True,
    help="Thư mục chứa các file SQLite.",
)
@click.option(
    "--output-dir",
    default="output_logs/raw_logs",
    show_default=True,
    help="Thư mục lưu raw logs JSON.",
)
@click.option(
    "--limit",
    default=None,
    type=int,
    help="Chỉ chạy N sample đầu tiên mỗi file (để test nhanh).",
)
@click.option(
    "--all",
    "run_all",
    is_flag=True,
    default=False,
    help="Chạy toàn bộ mẫu trong benchmark.",
)
@click.option(
    "--use-nlsql",
    is_flag=True,
    default=False,
    help="Sử dụng API của nlsql để sinh SQL thay vì mock.",
)
@click.option(
    "--resume/--no-resume",
    default=True,
    help="Tự động bỏ qua các sample đã chạy thành công trước đó.",
)
def main(benchmark, benchmark_dir, model, db_dir, output_dir, limit, run_all, use_nlsql, resume):
    """T2SQL Failure Attribution Pipeline — Entry Point."""

    db_dir_path = ROOT_DIR / db_dir
    output_dir_path = ROOT_DIR / output_dir
    output_dir_path.mkdir(parents=True, exist_ok=True)

    benchmark_files = []
    if benchmark_dir:
        b_dir = ROOT_DIR / benchmark_dir
        if b_dir.exists() and b_dir.is_dir():
            benchmark_files = list(b_dir.glob("*.json"))
    elif benchmark:
        b_path = ROOT_DIR / benchmark
        if b_path.exists():
            benchmark_files = [b_path]
    
    if not benchmark_files:
        console.print("[red]Không tìm thấy file dataset nào để chạy![/red]")
        sys.exit(1)

    console.rule("[bold cyan]T2SQL Failure Attribution Pipeline")
    console.print(f"[dim]Total Datasets: [/]{len(benchmark_files)}")
    console.print(f"[dim]Model         : [/]{model}")
    console.print(f"[dim]DB Dir        : [/]{db_dir_path}")
    console.print(f"[dim]Output        : [/]{output_dir_path}")
    console.print(f"[dim]Use NLSQL     : [/]{use_nlsql}")
    console.print(f"[dim]Resume        : [/]{resume}\n")

    classifier = RootCauseClassifier()
    
    # Load checkpoint
    checkpoint_file = output_dir_path / "resume_checkpoint.json"
    results = []
    processed_keys = set()
    
    if resume and checkpoint_file.exists():
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                results = json.load(f)
                processed_keys = {f"{r['dataset']}::{r['id']}" for r in results}
            console.print(f"[green]Loaded {len(results)} previously processed samples from checkpoint.[/green]")
        except Exception as e:
            console.print(f"[red]Error loading checkpoint: {e}[/red]")

    for bench_file in benchmark_files:
        console.print(f"\n[bold yellow]Processing Dataset:[/] {bench_file.name}")
        with open(bench_file, encoding="utf-8") as f:
            try:
                samples = json.load(f)
            except Exception as e:
                console.print(f"[red]Error loading {bench_file.name}: {e}[/red]")
                continue

        if not run_all and limit:
            samples = samples[:limit]
        elif not run_all and not limit:
            samples = samples[:5]
            console.print("[yellow]⚠ Chưa chỉ định --limit hoặc --all. Mặc định chạy 5 samples.[/]")

        # Count how many are left to process
        samples_to_run = []
        for i, sample in enumerate(samples):
            s_id = str(sample.get("id", i))
            sample["_index_id"] = s_id
            if resume and f"{bench_file.name}::{s_id}" in processed_keys:
                continue
            samples_to_run.append(sample)
            
        if not samples_to_run:
            console.print(f"[green]All samples in {bench_file.name} already processed.[/green]")
            continue

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(f"Running {bench_file.name}...", total=len(samples_to_run))

            for sample in samples_to_run:
                tracker = run_pipeline_for_sample(
                    sample, 
                    model, 
                    db_dir_path, 
                    use_nlsql_api=use_nlsql
                )

                label, explanation = classifier.classify(tracker.log)

                # Save log
                saved_path = tracker.save(output_dir=output_dir_path)
                
                # Add to results and checkpoint
                result_item = {
                    "dataset": bench_file.name,
                    "id": sample["_index_id"], 
                    "root_cause": label.name if hasattr(label, 'name') else str(label), 
                    "path": str(saved_path)
                }
                results.append(result_item)
                
                with open(checkpoint_file, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)

                progress.advance(task)

    # Summary table
    console.print()
    table = Table(title="Pipeline Results", show_lines=True)
    table.add_column("Dataset", style="blue")
    table.add_column("Sample ID", style="cyan")
    table.add_column("Root Cause", style="magenta")
    table.add_column("Log File", style="dim")

    for r in results:
        table.add_row(str(r["dataset"]), str(r["id"]), str(r["root_cause"]), Path(r["path"]).name)

    console.print(table)
    console.rule("[bold green]Done")


if __name__ == "__main__":
    main()
