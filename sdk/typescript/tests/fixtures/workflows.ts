export { CodeReviewWorkflow, SupportTriageWorkflow, AgentWorkflowsTrigger } from '../../src/examples/workflows';
import { Budget, WorkflowGateway } from '../../src/workflows';

// Use an existing trial policy to exercise tool and budget enforcement end to end.
export async function ResearchWorkflow(request: { topic: string; token_limit?: number; cost_limit_usd?: number }): Promise<string> {
  const gateway = new WorkflowGateway(new Budget(request.token_limit, request.cost_limit_usd));
  await gateway.tool('research', { topic: request.topic });
  return gateway.text(request.topic, { model: 'demo-openai' });
}
