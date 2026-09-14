const assert = require('node:assert/strict');
const test = require('node:test');

const {
  buildGraphUrl,
  relationControlState,
  relationVisual,
} = require('../static/graph-relations.js');

test('graph URL sends one selected relation type only for an enabled overlay', () => {
  assert.equal(
    buildGraphUrl({
      threshold: '0.8',
      clusterMode: 'raw',
      showDeclared: true,
      relationType: 'depends',
    }),
    '/api/graph?threshold=0.8&cluster=raw&show_declared=1&relation_types=depends',
  );
  assert.equal(
    buildGraphUrl({
      threshold: '0.8',
      clusterMode: 'raw',
      showDeclared: true,
      relationType: '',
    }),
    '/api/graph?threshold=0.8&cluster=raw&show_declared=1',
  );
  assert.equal(
    buildGraphUrl({
      threshold: '0.8',
      clusterMode: 'raw',
      showDeclared: false,
      relationType: 'depends',
    }),
    '/api/graph?threshold=0.8&cluster=raw&show_declared=0',
  );
});

test('relation visuals group types semantically and retain a fallback', () => {
  assert.equal(relationVisual('part_of').family, 'structure');
  assert.equal(relationVisual('depends').family, 'dependency');
  assert.equal(relationVisual('connects_to').family, 'integration');
  assert.equal(relationVisual('supersedes').family, 'evolution');
  assert.equal(relationVisual('contradicts').family, 'conflict');
  assert.equal(relationVisual('custom_relation').family, 'other');
});

test('typed relation selector is disabled only while the overlay is off', () => {
  assert.deepEqual(relationControlState(false, 'depends'), {
    disabled: true,
    accent: '#64748b',
    family: 'off',
  });
  assert.equal(relationControlState(true, '').family, 'all');
  assert.equal(relationControlState(true, 'depends').family, 'dependency');
});
