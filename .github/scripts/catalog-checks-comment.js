// Posts, updates or resolves the one catalog-checks comment on a pull request.
// Called from catalog-checks-comment.yml through actions/github-script.
//
// Everything read from the report was produced by pull-request code, so it is
// treated as data: the PR number is trusted only after the PR's head is matched
// against the checked commit, and logs are shown only inside code fences.

const fs = require('fs');
const path = require('path');

const MARKER = '<!-- catalog-checks-report -->';
const MAX_LOG_LINES = 40;

// Order is the order the author should fix things in: a generator crash hides
// everything else, and regenerating the README is always the last step.
const CHECKS = [
  {
    key: 'regen',
    log: 'build.txt',
    title: 'The README generator failed',
    help: () =>
      'Running `python3 scripts/build-readme.py` exited with an error, usually because a line in a ' +
      '`categories/*.md` file is malformed. Entries must follow the format in CONTRIBUTING.md:\n\n' +
      '```markdown\n- [Name](https://github.com/owner/repo) - Domain: what it does, in one sentence.\n```',
  },
  {
    key: 'tags',
    log: 'tags.txt',
    title: 'A tag value is not in the vocabulary',
    help: () =>
      'Tags are optional, but every value must be one listed in `tags.json` (the table is in ' +
      'CONTRIBUTING.md). Fix the spelling, pick a listed value, or remove the tag block.',
  },
  {
    key: 'parse',
    log: 'parse.txt',
    title: 'An entry line does not match the entry format',
    help: () =>
      'The README generator could not parse an entry, so that entry silently loses its badge and ' +
      'index listing. Use the exact entry format documented in CONTRIBUTING.md.',
  },
  {
    key: 'duplicates',
    log: 'duplicates.txt',
    title: 'The same repository is listed more than once',
    help: () =>
      'Each project belongs in exactly one category file. Keep the entry in the category that fits ' +
      'best and remove the other copy.',
  },
  {
    key: 'audit',
    log: 'audit.txt',
    title: 'A tag is not supported by the entry text',
    help: () =>
      'An `agent` tag must be earned by the entry itself: its name or description has to say which ' +
      'coding agent it targets. Either say so in the description or remove the tag. Write tags in ' +
      'source form (`` `{agent: claude-code}` ``), never as rendered badges.',
  },
  {
    key: 'tests',
    log: 'tests.txt',
    title: 'The script tests failed',
    help: () => 'The offline tests under `scripts/tests/` failed. Run `python3 -m unittest discover -s scripts/tests` locally.',
  },
  {
    key: 'drift',
    log: 'drift.txt',
    lang: 'diff',
    title: 'README.md does not match the category files',
    help: ({ readmeOnly }) =>
      readmeOnly
        ? '`README.md` is **generated** from `categories/*.md`, and this pull request edits only ' +
          '`README.md` — the next regeneration would erase your entry. Move it into the matching ' +
          '`categories/<category>.md` file (plain text, no badges or star counts), then run:\n\n' +
          '```sh\npython3 scripts/build-readme.py\n```\n\nand commit both files.'
        : '`README.md` is generated from `categories/*.md` and must be regenerated after editing them. ' +
          'Please do not edit it by hand. Run:\n\n' +
          '```sh\npython3 scripts/build-readme.py\n```\n\nand commit the updated `README.md`. If your ' +
          'branch is behind `main`, merge or rebase `main` first, then regenerate.',
  },
];

function read(dir, name) {
  try {
    return fs.readFileSync(path.join(dir, name), 'utf8');
  } catch {
    return '';
  }
}

// A fence longer than any backtick run in the text, so the log cannot close it.
function fenced(text, lang = 'text') {
  let lines = text.replace(/\s+$/, '').split('\n');
  let more = '';
  if (lines.length > MAX_LOG_LINES) {
    more = `\n_… ${lines.length - MAX_LOG_LINES} more lines in the workflow log._`;
    lines = lines.slice(0, MAX_LOG_LINES);
  }
  const body = lines.join('\n');
  const longest = Math.max(0, ...(body.match(/`+/g) || []).map((s) => s.length));
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return `${fence}${lang}\n${body}\n${fence}${more}`;
}

function failureBody({ failed, readmeOnly, runUrl, guideUrl, sha }) {
  const sections = failed.map((check, i) => {
    const log = check.logText.trim() ? `\n\n${fenced(check.logText, check.lang)}` : '';
    return `#### ${i + 1}. ${check.title}\n\n${check.help({ readmeOnly })}${log}`;
  });
  return [
    MARKER,
    '### ❌ Catalog checks failed',
    '',
    'Thanks for the submission! This pull request does not pass the catalog checks yet. ' +
      '**Please fix the issues below and push to this branch** — a maintainer will review it once ' +
      'the checks pass. This comment updates itself on every push.',
    '',
    sections.join('\n\n'),
    '',
    '---',
    `[Contributing guide](${guideUrl}) · [Workflow run](${runUrl}) · checked commit \`${sha.slice(0, 7)}\``,
  ].join('\n');
}

function passBody({ runUrl, sha }) {
  return [
    MARKER,
    '### ✅ Catalog checks pass',
    '',
    `The issues reported earlier are fixed as of \`${sha.slice(0, 7)}\` — thank you. ` +
      'A maintainer will review this pull request.',
    '',
    `[Workflow run](${runUrl})`,
  ].join('\n');
}

module.exports = async function run({ github, context, core, reportDir = process.env.REPORT_DIR }) {
  const wr = context.payload.workflow_run;
  const { owner, repo } = context.repo;

  const prNumber = Number(read(reportDir, 'pr-number').trim());
  if (!Number.isInteger(prNumber) || prNumber <= 0) {
    core.info('No report from the check run; nothing to comment on.');
    return;
  }
  const reportedSha = read(reportDir, 'head-sha').trim();
  const { data: pull } = await github.rest.pulls.get({ owner, repo, pull_number: prNumber });
  if (pull.head.sha !== wr.head_sha || reportedSha !== wr.head_sha) {
    // Either the report names a PR that did not produce it, or the author has
    // pushed again since; that newer run will comment instead.
    core.info(`PR #${prNumber} head ${pull.head.sha} is not the checked commit ${wr.head_sha}; skipping.`);
    return;
  }

  const outcomes = Object.fromEntries(
    read(reportDir, 'outcomes')
      .split('\n')
      .map((l) => l.trim().split('='))
      .filter(([k, v]) => k && v),
  );
  const changed = read(reportDir, 'changed-files').split('\n').filter(Boolean);
  const readmeOnly = changed.includes('README.md') && !changed.some((f) => f.startsWith('categories/'));

  const failed = CHECKS.filter((c) => outcomes[c.key] === 'failure').map((c) => ({
    ...c,
    logText: read(reportDir, c.log),
  }));

  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: prNumber,
    per_page: 100,
  });
  const existing = comments.find((c) => c.user && c.user.type === 'Bot' && c.body && c.body.includes(MARKER));

  const runUrl = wr.html_url;
  const guideUrl = `${context.serverUrl}/${owner}/${repo}/blob/${pull.base.ref}/CONTRIBUTING.md`;

  if (failed.length === 0) {
    // Only speak up on success to close out an earlier failure report.
    if (existing && !existing.body.includes('✅')) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body: passBody({ runUrl, sha: wr.head_sha }) });
      core.info(`Marked the report on PR #${prNumber} as resolved.`);
    }
    return;
  }

  const body = failureBody({ failed, readmeOnly, runUrl, guideUrl, sha: wr.head_sha });
  if (existing) {
    await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
    core.info(`Updated the report on PR #${prNumber}.`);
  } else {
    await github.rest.issues.createComment({ owner, repo, issue_number: prNumber, body });
    core.info(`Posted a report on PR #${prNumber}.`);
  }
};

// Exported for the local test.
module.exports.fenced = fenced;
module.exports.MARKER = MARKER;
