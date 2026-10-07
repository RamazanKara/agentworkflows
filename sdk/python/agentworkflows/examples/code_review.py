"""Review a diff and wait for a human decision; never execute, merge, or post code."""

from dataclasses import dataclass

from temporalio import workflow

from agentworkflows.workflows import ApprovalWorkflow, WorkflowGateway


@dataclass
class CodeReviewRequest:
    diff: str
    model: str = "demo-openai"


@workflow.defn
class CodeReviewWorkflow(ApprovalWorkflow):
    @workflow.run
    async def run(self, request: CodeReviewRequest) -> dict:
        review = await WorkflowGateway().text(
            f"Review this diff for correctness and security. Cite lines and suggest tests.\n{request.diff}",
            model=request.model,
        )
        approved = await self.approval(review)
        return {"approved": approved, "review": review, "reviewer": self.reviewer}
