const assert = require('node:assert/strict');
const test = require('node:test');

const {
  BusyHttpError,
  HttpResponseError,
  fetchJsonWithBusyRetry,
  retryAfterMilliseconds,
} = require('../static/http.js');

function response(status, payload, retryAfter = null) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLowerCase() === 'retry-after' ? retryAfter : null },
    json: async () => payload,
  };
}

test('busy graph keeps current data until one retry succeeds', async () => {
  const currentGraph = { nodes: [{ id: 'visible' }], edges: [] };
  let displayedGraph = currentGraph;
  const waits = [];
  const states = [];
  const responses = [
    response(503, { code: 'heavy_work_busy', error: 'capacity busy' }, '1'),
    response(200, { nodes: [{ id: 'updated' }], edges: [] }),
  ];

  const nextGraph = await fetchJsonWithBusyRetry('/api/graph', {
    fetchImpl: async () => responses.shift(),
    sleep: async (milliseconds) => {
      waits.push(milliseconds);
      assert.equal(displayedGraph, currentGraph);
    },
    onBusy: (state) => states.push(state),
  });
  displayedGraph = nextGraph;

  assert.deepEqual(waits, [1000]);
  assert.deepEqual(states, [{ attempt: 1, retryAfterMs: 1000 }]);
  assert.equal(displayedGraph.nodes[0].id, 'updated');
});

test('second busy response fails explicitly and leaves graph data intact', async () => {
  const currentGraph = { nodes: [{ id: 'visible' }], edges: [] };
  let displayedGraph = currentGraph;

  await assert.rejects(
    fetchJsonWithBusyRetry('/api/graph', {
      fetchImpl: async () => response(
        503, { code: 'heavy_work_busy', error: 'capacity busy' }, '0'
      ),
      sleep: async () => {},
    }).then((nextGraph) => { displayedGraph = nextGraph; }),
    (error) => error instanceof BusyHttpError && error.status === 503,
  );

  assert.equal(displayedGraph, currentGraph);
});

test('non-busy HTTP errors are not retried', async () => {
  let calls = 0;
  await assert.rejects(
    fetchJsonWithBusyRetry('/api/projection', {
      fetchImpl: async () => {
        calls += 1;
        return response(500, { error: 'projection exploded' });
      },
      sleep: async () => assert.fail('must not sleep'),
    }),
    (error) => error instanceof HttpResponseError
      && error.status === 500
      && error.message.includes('projection exploded'),
  );
  assert.equal(calls, 1);
});

test('Retry-After seconds are parsed and invalid values use one second', () => {
  assert.equal(retryAfterMilliseconds(response(503, {}, '1.5')), 1500);
  assert.equal(retryAfterMilliseconds(response(503, {}, 'invalid')), 1000);
  assert.equal(retryAfterMilliseconds(response(503, {}, null)), 1000);
});
