"""Import-time shim for DIN-SQL, applied by experiments/setup_server.sh.

Why a shim rather than a rewrite
--------------------------------
Upstream `DIN-SQL.py` cannot be imported at all:

  * it parses `sys.argv` at module level and raises if the flags are absent;
  * line 444 reads `API_KEY = #key`, which is not valid Python — the file as
    published is a syntax error until the author's own key is pasted in;
  * it calls `openai.ChatCompletion.create`, removed in the OpenAI SDK >= 1.0.

setup_server.sh replaces exactly those three things and nothing else — the same
transport-only intervention already applied to MAC-SQL and MAG-SQL. Every
prompt, every few-shot exemplar, every sampling parameter and the whole control
flow stay byte-identical to the pinned commit, so `git -C baselines/DIN-SQL
diff` shows the full extent of the change.

This file holds the replacement text as reviewable source rather than burying
it in a heredoc.
"""

# The upstream block this replaces, matched verbatim before substitution.
UPSTREAM_BLOCK = '''if sys.argv[1] == "--dataset" and sys.argv[3] == "--output":
    DATASET_SCHEMA = sys.argv[2]+"tables.json"
    DATASET = sys.argv[2]+"dev.json"
    OUTPUT_FILE = sys.argv[4]
else:
    raise Exception("Please use this format python CoT.py --dataset data/ --output predicted_sql.txt")

API_KEY = #key
os.environ["OPENAI_API_KEY"] = API_KEY
openai.api_key = os.getenv("OPENAI_API_KEY")
'''

HEADER = '''# --- harness shim (attributability audit) ----------------------------------
# Upstream parsed sys.argv here and assigned an API key inline. Both now come
# from the environment. Nothing outside this block is modified.
DATASET_SCHEMA = os.environ.get("DINSQL_TABLES_JSON", "")
DATASET = os.environ.get("DINSQL_DEV_JSON", "")
OUTPUT_FILE = os.environ.get("DINSQL_OUTPUT", "predicted_sql.txt")


class _ChatCompletionCompat:
    """The openai<1.0 call shape on top of the >=1.0 SDK.

    Transport only. GPT4_generation and GPT4_debug below keep upstream's exact
    sampling parameters; the sole substitution is the model id, because
    upstream hardcodes "gpt-4" and every system in a comparison must run on the
    same backbone or the comparison measures the LLM rather than the pipeline.
    """

    dropped_params = []

    @staticmethod
    def create(**kw):
        from openai import OpenAI

        kw = dict(kw)
        kw["model"] = os.environ.get("DINSQL_MODEL") or kw.get("model")
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                        base_url=os.getenv("OPENAI_API_BASE") or None)
        try:
            r = client.chat.completions.create(**kw)
        except Exception as e:
            # Some OpenAI-compatible servers and some newer models reject a
            # parameter upstream sends (max_tokens was renamed; several
            # reasoning models refuse a non-default temperature). Drop only the
            # parameter the server actually named, once, and record that it
            # happened -- a silent retry with different sampling would change
            # what is being measured while leaving no trace in the logs.
            msg = str(e)
            retry = dict(kw)
            if "max_tokens" in msg and "max_tokens" in retry:
                retry["max_completion_tokens"] = retry.pop("max_tokens")
            if "temperature" in msg and "temperature" in retry:
                retry.pop("temperature")
            if retry == kw:
                raise
            _ChatCompletionCompat.dropped_params.append(msg[:200])
            r = client.chat.completions.create(**retry)
        return {"choices": [{"message": {"content": r.choices[0].message.content}}]}


openai.ChatCompletion = _ChatCompletionCompat
# --- end harness shim ------------------------------------------------------
'''
