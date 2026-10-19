const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const run = require('./catalog-checks-comment.js');

test('parse failure renders entry-format guidance without a GitHub network call', async () => {
  const reportDir = fs.mkdtempSync(path.join(os.tmpdir(), 'catalog-report-'));
  const created = [];
  try {
    fs.writeFileSync(path.join(reportDir, 'pr-number'), '17\n');
    fs.writeFileSync(path.join(reportDir, 'head-sha'), '1234567890abcdef\n');
    fs.writeFileSync(path.join(reportDir, 'outcomes'), 'regen=success\nparse=failure\n');
    fs.writeFileSync(path.join(reportDir, 'parse.txt'), '⚠️  parse: categories/routing.md:5\n');
    fs.writeFileSync(path.join(reportDir, 'changed-files'), 'categories/routing.md\n');

    const github = {
      paginate: async () => [],
      rest: {
        pulls: { get: async () => ({ data: { head: { sha: '1234567890abcdef' }, base: { ref: 'main' } } }) },
        issues: {
          listComments: async () => [],
          createComment: async (request) => created.push(request),
        },
      },
    };
    const context = {
      payload: { workflow_run: { head_sha: '1234567890abcdef', html_url: 'https://example.test/run/1' } },
      repo: { owner: 'owner', repo: 'awesome-jev' },
      serverUrl: 'https://example.test',
    };

    await run({ github, context, core: { info() {} }, reportDir });

    assert.equal(created.length, 1);
    assert.match(created[0].body, /entry line does not match the entry format/i);
    assert.match(created[0].body, /categories\/routing\.md:5/);
    assert.doesNotMatch(created[0].body, /Catalog checks pass/);
  } finally {
    fs.rmSync(reportDir, { recursive: true, force: true });
  }
});
