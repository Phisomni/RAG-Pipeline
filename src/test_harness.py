import time
import csv
import os
import subprocess
import re
import psutil
from datetime import datetime
from itertools import product

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG_DIR = os.path.join(PROJECT_ROOT, "experiment_logs")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "llm_outputs")
CONFIG_FILE = os.path.join(PROJECT_ROOT, "last_indexed_config.json")
LOG_FILE = None
LLM_OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"llm_outputs_grid_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# log data from an experiment into log file
def log_result(row: dict):
    global LOG_FILE

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    if not LOG_FILE:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        LOG_FILE = os.path.join(LOG_DIR, f"rag_test_results_{timestamp}.csv")
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            writer.writeheader()

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        writer.writerow(row)

    print("Logged experiment to CSV.")

# store previous time and memory for reuse
last_time = None
last_memory = None

# run grid test for all variable input combinations
def run_experiment(exp_id, embed_model, chunk_size, overlap, vector_db, llm_model, question, 
                   system_prompt="default", embed_index_time=None, embed_index_memory=None):
    print(f"\n▶ Running: {embed_model} | chunk={chunk_size} | overlap={overlap} | db={vector_db} | llm={llm_model}")
    row = {
        "exp_id": exp_id,
        "embed_model": embed_model,
        "chunk_size": chunk_size,
        "overlap": overlap,
        "vector_db": vector_db,
        "llm_model": llm_model,
        "question": question,
        "system_prompt": system_prompt,
        "embedding_and_index_time_sec": round(embed_index_time, 2) if embed_index_time else "",
        "embedding_and_index_memory_mb": round(embed_index_memory, 2) if embed_index_memory else ""
    }

    # query timing and memory
    start = time.time()
    result = subprocess.run([
        "python", os.path.join(PROJECT_ROOT, "src", "test_query.py"),
        "--model", embed_model,
        "--chunk_size", str(chunk_size),
        "--overlap", str(overlap),
        "--source", vector_db,
        "--llm_model", llm_model,
        "--question", question,
        "--system_prompt", system_prompt
    ], capture_output=True, text=True)
    query_time = time.time() - start
    row["query_time_sec"] = round(query_time, 2)

    output = result.stdout.strip()

    # parse <QUERY_MEMORY_MB>
    mem_match = re.search(r"<QUERY_MEMORY_MB>(.*?)</QUERY_MEMORY_MB>", output)
    if mem_match:
        try:
            row["query_memory_mb"] = round(float(mem_match.group(1)), 2)
        except:
            row["query_memory_mb"] = ""
    else:
        row["query_memory_mb"] = ""

    # total runtime
    row["total_runtime_sec"] = round(query_time + (embed_index_time or 0), 2)

    # parse <LLM_RESPONSE>
    match = re.search(r"<LLM_RESPONSE>(.*?)</LLM_RESPONSE>", output, re.DOTALL)
    if match:
        response_text = match.group(1).strip()
    else:
        response_text = "(No response)"
    row["llm_response_summary"] = response_text[:100].replace("\n", " ")

    # save full response to shared file
    with open(LLM_OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(f"--- Experiment {exp_id} ---\n")
        f.write(f"Model: {embed_model} | Chunk: {chunk_size} | Overlap: {overlap} | DB: {vector_db} | LLM: {llm_model}\n")
        f.write(f"Prompt: {system_prompt}\n")
        f.write(f"Question: {question}\n")
        f.write("LLM Response:\n")
        f.write(response_text + "\n\n")

    log_result(row)


# EXPERIMENT GRID CONFIGURATION
embed_models = ["all-MiniLM-L6-v2", "all-mpnet-base-v2", "intfloat/e5-base-v2"]
chunk_sizes = [200, 500, 1000]
overlaps = [0, 50, 100]
vector_dbs = ["redis", "faiss", "chroma"]
llm_models = ["mistral", "llama2"]
questions = [
    "What are ACID properties in databases?"
]
system_prompts = [
    "default",
    "You are a database expert tutor. Answer clearly and concisely using the course materials."
]

# MAIN LOOP
last_embedding_config = {}
experiments = list(product(embed_models, chunk_sizes, overlaps, vector_dbs, llm_models, questions, system_prompts))
total_experiments = len(experiments)

for i, (embed_model, chunk_size, overlap, vector_db, llm_model, question, system_prompt) in enumerate(experiments, start=1):
    print(f"\nExperiment {i}/{total_experiments}")

    # set current data configuration
    current_config = {
        "embed_model": embed_model,
        "chunk_size": chunk_size,
        "overlap": overlap,
        "vector_db": vector_db
    }

    # regen data for configuration different from previous
    if current_config != last_embedding_config:
        print("Regenerating embeddings + indexing...")

        start_time = time.time()
        proc = subprocess.Popen([
            "python", os.path.join(PROJECT_ROOT, "src", "load_dbs.py"),
            "--model", embed_model,
            "--chunk_size", str(chunk_size),
            "--overlap", str(overlap),
            "--vector_db", vector_db
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        try:
            p = psutil.Process(proc.pid)
            peak_mem = 0
            while proc.poll() is None:
                mem = p.memory_info().rss / 1024 / 1024
                peak_mem = max(peak_mem, mem)
                time.sleep(0.1)
        except Exception as e:
            print(f"Could not capture accurate memory: {e}")
            peak_mem = None

        proc.communicate()
        embed_index_time = time.time() - start_time
        embed_index_memory = peak_mem

        last_embedding_config = current_config.copy()
        last_time = embed_index_time
        last_memory = embed_index_memory
    else:
        # resuse previous data configuration if same
        print("Reusing existing embeddings and DB index")
        embed_index_time = last_time
        embed_index_memory = last_memory

    # run experiment
    run_experiment(
        i, embed_model, chunk_size, overlap, vector_db, llm_model,
        question, system_prompt,
        embed_index_time=embed_index_time,
        embed_index_memory=embed_index_memory
    )
