from __future__ import annotations

import json
import re
from pathlib import Path

from benchmark_store import BenchmarkError, digest, execute, git, now, resolve, save


def github_repo(repo: Path) -> str:
    url = git(repo, 'remote', 'get-url', 'origin').decode().strip()
    match = re.fullmatch(r'(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?', url)
    if not match:
        raise BenchmarkError('Task recovery supports a GitHub origin only; supply a task file')
    return match[1]


def request(command: list[str]):
    return json.loads(execute(command))


def issue_references(text: str, repository: str) -> list[str]:
    references = [f'https://github.com/{repo}/issues/{number}' for repo, number in
                  re.findall(r'https://github\.com/([\w.-]+/[\w.-]+)/issues/([1-9][0-9]*)\b', text)]
    references.extend(f'https://github.com/{repo}/issues/{number}' for repo, number in
                      re.findall(r'(?<![\w/])([\w.-]+/[\w.-]+)#([1-9][0-9]*)\b', text))
    references.extend(f'https://github.com/{repository}/issues/{number}' for number in
                      re.findall(r'(?<![\w/])#([1-9][0-9]*)\b', text))
    return references


def recover(repo: Path, head: str, *, requester=request) -> dict:
    repository, sha = github_repo(repo), resolve(repo, head)
    errors, candidates = [], []
    try:
        pulls = requester(['gh', 'api', f'repos/{repository}/commits/{sha}/pulls'])
    except (BenchmarkError, ValueError, OSError) as exc:
        pulls = []
        errors.append(str(exc))
    for pull in pulls:
        number = pull['number']
        candidate = {'number': number, 'url': pull['html_url'], 'title': pull['title'],
                     'body': pull.get('body') or '', 'created_at': pull.get('created_at'),
                     'updated_at': pull.get('updated_at'), 'issues': []}
        references = issue_references(candidate['body'], repository)
        try:
            details = requester(['gh', 'pr', 'view', str(number), '--repo', repository,
                                 '--json', 'closingIssuesReferences'])
            references.extend(issue['url'] for issue in details.get('closingIssuesReferences', []))
        except (BenchmarkError, ValueError, OSError) as exc:
            errors.append(f'PR {number} linked issue recovery: {exc}')
        for url in dict.fromkeys(references):
            try:
                value = requester(['gh', 'issue', 'view', url, '--json', 'number,title,body,url,createdAt,updatedAt'])
                candidate['issues'].append(value)
            except (BenchmarkError, ValueError, OSError) as exc:
                errors.append(f'PR {number} linked issue {url}: {exc}')
        candidates.append(candidate)
    return {'schema': 1, 'repository': repository, 'head': sha, 'recovered_at': now(),
            'candidates': candidates, 'errors': errors, 'selection_required': True,
            'limitation': 'GitHub returns current descriptions. They may contain later edits. The parent must select original requirements and remove later outcomes before setup.'}


def select(value: dict, number: int, task: Path, *, selected_text: str) -> dict:
    candidates = [c for c in value['candidates'] if c['number'] == number]
    if len(candidates) != 1 or not selected_text.strip():
        raise BenchmarkError('Select one recovered PR and provide nonempty original requirements')
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text(selected_text)
    candidate = candidates[0]
    provenance = {'kind': 'github-parent-selection', 'repository': value['repository'], 'head': value['head'],
                  'pr': candidate['url'], 'issues': [i['url'] for i in candidate['issues']],
                  'recovered_at': value['recovered_at'], 'description_updated_at': candidate['updated_at'],
                  'task_sha256': digest(selected_text.encode()),
                  'candidate_sha256': digest(json.dumps(candidate, sort_keys=True).encode()),
                  'selection': 'Parent selected original requirements; later reviews and fixes excluded'}
    save(task.with_suffix(task.suffix + '.provenance.json'), provenance)
    return provenance
