export class GatewayError extends Error {
  readonly statusCode: number;
  readonly reason?: string;
  readonly requestId?: string;
  readonly retryAfter?: number;

  constructor(readonly response: Response, readonly detail: Record<string, unknown>) {
    const reason = typeof detail.reason === 'string' ? detail.reason : undefined;
    const message = typeof detail.message === 'string' ? detail.message : 'Gateway request failed';
    super(`${response.status} ${message}${reason ? ` (${reason})` : ''}`);
    this.name = 'GatewayError';
    this.statusCode = response.status;
    this.reason = reason;
    this.requestId = typeof detail.request_id === 'string'
      ? detail.request_id : response.headers.get('X-Request-ID') ?? undefined;
    const delay = response.headers.get('Retry-After')?.trim();
    if (delay && /^\d+$/.test(delay)) this.retryAfter = Number(delay);
  }
}

export class GatewayRetryAfterError extends GatewayError {
  constructor(error: GatewayError) {
    super(error.response, error.detail);
    this.name = 'GatewayRetryAfterError';
  }
}

export class GatewayTransportError extends Error {
  constructor() {
    super('Cannot reach the gateway; check AGENTWORKFLOWS_URL and gateway health.');
    this.name = 'GatewayTransportError';
  }
}
