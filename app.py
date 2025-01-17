from dotenv import load_dotenv
from llama_index.core.schema import NodeWithScore
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex
from llama_index.core.response_synthesizers import CompactAndRefine
from llama_index.core.postprocessor.llm_rerank import LLMRerank
from llama_index.core.workflow import (
    Context,
    Workflow,
    StartEvent,
    StopEvent,
    step,
    Event
)
from llama_index.core.workflow.utils import get_steps_from_class, get_steps_from_instance
from llama_index.llms.groq import Groq
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.utils.workflow import draw_all_possible_flows, draw_most_recent_execution
import nest_asyncio
import asyncio
import os

_ = load_dotenv()

os.environ["TOKENIZERS_PARALLELISM"] = "false"

# Define the events
class RetrieverEvent(Event):
    """Result of running retrieval"""
    nodes: list[NodeWithScore]

class RerankEvent(Event):
    """Result of running reranking on retrieved nodes"""
    nodes: list[NodeWithScore]

#  Define the workflow
class RAGWorkflow(Workflow):
    @step
    async def ingest(self, ctx: Context, ev: StartEvent) -> StopEvent | None:
        """Entry point to ingest a document, triggered by a StartEvent with `dirname`."""
        dirname = ev.get("dirname")
        if not dirname:
            return None

        documents = SimpleDirectoryReader(dirname).load_data()
        index = VectorStoreIndex.from_documents(
            documents=documents,
            embed_model=HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5"),
        )
        return StopEvent(result=index)

    @step
    async def retrieve(
        self, ctx: Context, ev: StartEvent
    ) -> RetrieverEvent | None:
        "Entry point for RAG, triggered by a StartEvent with `query`."
        query = ev.get("query")
        index = ev.get("index")

        if not query:
            return None

        print(f"Query the database with: {query}")

        # store the query in the global context
        await ctx.set("query", query)

        # get the index from the global context
        if index is None:
            print("Index is empty, load some documents before querying!")
            return None

        retriever = index.as_retriever(similarity_top_k=2)
        nodes = await retriever.aretrieve(query)
        print(f"Retrieved {len(nodes)} nodes.")
        return RetrieverEvent(nodes=nodes)

    @step
    async def rerank(self, ctx: Context, ev: RetrieverEvent) -> RerankEvent:
        # Rerank the nodes
        ranker = LLMRerank(
            choice_batch_size=5, top_n=3,
            # llm=Groq(model="llama-3.1-70b-versatile")
            llm=Groq(model="llama-3.2-90b-text-preview")
        )
        print(await ctx.get("query", default=None), flush=True)
        new_nodes = ranker.postprocess_nodes(
            ev.nodes, query_str=await ctx.get("query", default=None)
        )
        print(f"Reranked nodes to {len(new_nodes)}")
        print(new_nodes)
        return RerankEvent(nodes=new_nodes)

    @step
    async def synthesize(self, ctx: Context, ev: RerankEvent) -> StopEvent:
        """Return a streaming response using reranked nodes."""
        # llm = Groq(model="llama-3.1-70b-versatile")
        llm = Groq(model="llama-3.2-90b-text-preview")
        summarizer = CompactAndRefine(llm=llm, streaming=True, verbose=True)
        query = await ctx.get("query", default=None)
        response = await summarizer.asynthesize(query, nodes=ev.nodes)
        return StopEvent(result=response)

# Check if steps have __step_config attribute
workflow = RAGWorkflow()

steps = get_steps_from_class(RAGWorkflow)
if not steps:
    steps = get_steps_from_instance(workflow)
print(f"steps class :{steps}")

for step_name, step_func in steps.items():
    step_config = getattr(step_func, "__step_config", None)
    print(f"step config :{step_config}")
    if step_config is None:
        print(f"Step {step_name} is missing __step_config")

# Invoke the workflow and visualize
nest_asyncio.apply()
# Draw all possible flows
draw_all_possible_flows(RAGWorkflow, filename="multi_step_workflow.html")

# Draw the most recent execution
w = RAGWorkflow()
# Ingest the documents

async def main():
    index = await w.run(dirname="docs")
    result = await w.run(query="Whats the cash back amount for dental expenses?", index=index)
    async for chunk in result.async_response_gen():
        print(chunk, end="", flush=True)

asyncio.run(main())

draw_most_recent_execution(w, filename="rag_flow_recent.html")