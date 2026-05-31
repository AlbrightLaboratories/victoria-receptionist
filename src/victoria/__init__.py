"""Victoria Albright — virtual receptionist for AlbrightLab.

A FastAPI-backed conversational agent that talks to users through a
floating widget on the AlbrightLab website. Backed by a Triton inference
server (Llama-3.1-8B-Instruct or whatever model is loaded), with a
pgvector RAG index over site content and a Tavily/SearXNG web-search
fallback. When neither RAG nor web search produces a confident answer,
Victoria opens a GitHub issue in the main site repo so the team can
fill the knowledge gap.

Phase 2: NeMo-Curator fine-tune. The scaffolding here is wired so
training-loop output can drop in without rearchitecting.
"""

__version__ = "0.1.0"
