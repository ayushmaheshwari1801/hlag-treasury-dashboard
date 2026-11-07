"""Build and validate the portable TXT/CSV real-news workflow corpus.

This module never calls a provider. Source-only loaders do not load gold annotations
or the old fixture's frozen W2 analysis. Those are separate evaluation inputs.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / 'evaluation/data/real_workflow'
ORIGINAL_DATASET = ROOT / 'tests/fixtures/event_sequences_real_v1.json'
SMOKE_IDS = ('real-7645ea4d8dda', 'real-a114d8803587', 'real-50339f5e620a', 'real-5e84489fdd4c')
SOURCE_COLUMNS = ('source_id','stream','sequence_index','title','url','publisher','published_at','discovered_at',
                  'published_precision','body_path','body_sha256','archive_path','archive_sha256','archive_paragraphs',
                  'retrieved_at','text_mode','license')
GOLD_COLUMNS = ('source_id','event','scope','lifecycle','decision','kind','identity_confidence','state_confidence',
                'annotator','review_status','rationale','verification_qualification','annotation_version')
SCENARIO_COLUMNS = ('scenario','phase','cohort','ordinal','source_id','week_start','reference_time','expected_ingestion')

SMOKE_GOLD_COLUMNS = ('source_id','w1_expected_source','w2_allowed_categories','w2_expected_review_status',
    'w2_required_fact','w2_qualification','w3_expected_behavior','w3_permitted_numeric_observation',
    'w3_forbidden_interpretation','w4_constraint','annotation_status')
SMOKE_GUIDANCE = (
    ('real-7645ea4d8dda','Macroeconomic|Regulatory','The Federal Reserve will establish financing backed by PPP loans.',
     'The facility is announced, not yet operational; additional details are still pending.',
     'Factual-only is appropriate; a directional claim must retain the prospective qualification.','',
     'No numeric amount or actual completed lending is stated.',
     'If selected, describe the planned facility without invented size, rates, implementation or Hapag-Lloyd exposure.'),
    ('real-a114d8803587','Regulatory','The interim final capital-treatment rule neutralizes regulatory capital effects of PPPLF participation and specifies zero percent risk weight for PPP loans.',
     'The capital rule is effective immediately; it is a separate occurrence from the liquidity facility.',
     'A supported zero-percent risk-weight observation or a factual-only rule-change summary is acceptable.','0 percent risk weight for PPP loans; no previous comparison value is supplied.',
     'Do not treat the 30-day comment period as a measured Treasury impact, or invent a risk-weight delta.',
     'Keep the capital rule distinct from the lending facility; preserve its immediate effectiveness.'),
    ('real-50339f5e620a','Macroeconomic|Regulatory','The Paycheck Protection Program Liquidity Facility is fully operational and available to eligible financial institutions.',
     'Operation is confirmed; increased lending capacity and wider economic benefits are expected consequences.',
     'Factual-only operational progress is appropriate; no numeric lending quantity is stated.','',
     'Do not report expected increases in lending capacity as measured realized growth.',
     'Report implementation progress of the existing PPPLF occurrence, not a second new facility.'),
    ('real-5e84489fdd4c','Regulatory','A temporary rule change will allow certain bank directors and shareholders to apply for PPP loans, subject to the stated restrictions.',
     'The change applies only to PPP loans and does not remove general insider-lending restrictions.',
     'Factual-only regulatory eligibility progress is appropriate; no numeric material quantity is stated.','',
     'Do not invent loan volumes, eligibility thresholds or confirmation that loans were disbursed.',
     'Keep the insider-lending exception distinct from the capital rule and PPPLF facility; preserve its limited scope.'),
)
CASE_COLUMNS = ('case_id','workflow_or_contract','fixture_kind','source_selection','action','expected_result',
                'validation_entrypoint','quality_scope')
CONTRACT_CASES = (
    ('real-initial','W1/W2/W3/E/W4','authentic selected news passages','smoke initial',
     'Submit pre-cutoff intake and post-cutoff publication HTTP jobs, then read both results.',
     'Sources pass through real W2/W3/E and W4; each stage and any partial failure stays visible.',
     'evaluation.service_benchmark','Continuity and saved output; semantic gold is separately scoped.'),
    ('real-incremental','W1/W2/W3/E/W4','authentic selected news passages','smoke incremental',
     'Advance one reporting week and submit a new job against the same database.',
     'Prior articles remain; the new PPPLF update uses the existing occurrence; the insider rule stays separate.',
     'evaluation.service_benchmark','Real Event membership/lifecycle plus workflow continuity.'),
    ('event-to-weekly-brief','E/W4 historical handoff','authentic selected news passages','each original reporting week',
     'Ingest before the weekly cutoff, then replay the sources in a publication job after the cutoff.',
     'Eligible Event snapshots and evidence reach frozen W4 inputs and saved output; completed W2/W3/E makes zero extra calls.',
     'evaluation.service_benchmark','Operational handoff, bounded evidence and reuse; model label agreement is diagnostic.'),
    ('real-idempotent-replay','HTTP idempotency','same authentic inputs; no new samples','every service cohort',
     'Repeat the same POST body and Idempotency-Key, then reread the result.',
     'Same job identity and saved result; no extra provider calls.',
     'evaluation.service_benchmark','Replay contract; replay does not inflate unique-news count.'),
    ('full-temporal-corpus','All workflow stages and E history','authentic selected news passages','timeline: 72 sources / original reporting weeks',
     'Process all reporting-week cohorts in chronological order.',
     'No future source is supplied early; every original source enters once; Event state persists across cohorts.',
     'evaluation.service_benchmark','Cross-domain continuity; inherited E gold was originally annotated in two domain streams.'),
    ('restart-saved-result','HTTP restart','recorded synthetic providers','existing API fixtures',
     'Restart the application and GET the saved result.',
     'Identical result; zero provider calls.',
     'scripts/check_workflow_api.py::test_full_pipeline_persistent_result_and_safe_idempotent_retry','Deterministic API behavior, not real-model accuracy.'),
    ('interrupted-resume','HTTP recovery','recorded synthetic providers','existing API fixtures',
     'Interrupt an unfinished job and resume its saved attempt.',
     'Reuse saved collection; recovery POST is itself idempotent.',
     'scripts/check_workflow_api.py::test_interruption_recovers_saved_root_without_repeating_search','Deterministic recovery; no deliberate interruption of paid production jobs.'),
    ('busy-and-polling','HTTP concurrency','recorded synthetic providers','existing API fixtures',
     'Submit another job during an active job and poll the active job.',
     'Explicit busy response and stable job status; no parallel mutation of the shared database.',
     'scripts/check_workflow_api.py::test_concurrent_submit_busy_and_polling_during_work','API contract only.'),
    ('authentication-contract','HTTP auth/CORS/OpenAPI','recorded synthetic providers','existing API fixtures',
     'Exercise missing credentials, invalid bodies and the public API schema.',
     'Unauthorized/invalid requests fail explicitly; documented endpoints match OpenAPI.',
     'scripts/check_workflow_api.py::test_auth_cors_validation_and_openapi','API contract only.'),
    ('credit-stop','Provider failure','injected deterministic failure','existing API fixtures',
     'Simulate credit exhaustion during E backlog processing.',
     'Stop further work and skip automatic publication.',
     'scripts/check_workflow_api.py::test_event_backlog_stops_on_error_credit_or_no_progress','Failure handling; not evidence of provider availability.'),
    ('w3-major-change-boundaries','W3','synthetic labeled source cases','tests/fixtures/major_changes_news_context_v4.json',
     'Run the existing W3 benchmark with frozen W2 input.',
     'Retain supported major changes, reject unsupported quantities and distinguish output states.',
     'benchmark.py --suite w3-live','120 synthetic cases; not independent real-news W2/W3 full-flow accuracy.'),
    ('event-negative-and-conflict-contracts','E','synthetic sequence and deterministic checks','tests/fixtures/event_sequences_v3.json',
     'Exercise non-event, unconfirmed, conflict, correction and duplicate contracts.',
     'Preserve evidence qualification and occurrence identity; do not fabricate real-news examples for absent classes.',
     'scripts/check_events.py','Synthetic contract coverage complements the official-release corpus.'),
)


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def write_csv(path: Path, columns, rows):
    with path.open('w', encoding='utf8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path, columns):
    with path.open(encoding='utf8', newline='') as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != tuple(columns):
            raise ValueError(f'Unexpected CSV columns: {path.name}')
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f'Malformed CSV row: {path.name}')
    return rows


def safe_path(directory: Path, value: str) -> Path:
    root = directory.resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Corpus file path must remain inside the corpus directory')
    return path


def load_sources(directory: Path | str = DEFAULT_CORPUS) -> dict[str, dict]:
    """Read source inputs only; each returned ``source`` is an ArticleInput mapping."""
    directory = Path(directory)
    sources = {}
    for row in read_csv(directory / 'sources.csv', SOURCE_COLUMNS):
        source_id = row['source_id']
        if not re.fullmatch(r'real-[a-f0-9]{12}', source_id) or source_id in sources:
            raise ValueError('Invalid or duplicate source ID')
        body = safe_path(directory, row['body_path']).read_bytes()
        if digest(body) != row['body_sha256']:
            raise ValueError(f'Source body hash mismatch: {source_id}')
        published = datetime.fromisoformat(row['published_at'])
        discovered = datetime.fromisoformat(row['discovered_at'])
        if published.tzinfo is None or discovered.tzinfo is None or published > discovered:
            raise ValueError(f'Invalid source timestamps: {source_id}')
        sources[source_id] = {
            'source_id': source_id, 'stream': row['stream'], 'sequence_index': int(row['sequence_index']),
            'discovered_at': row['discovered_at'],
            'source': {'title': row['title'], 'body': body.decode('utf8'), 'url': row['url'],
                       'source': row['publisher'], 'published_at': row['published_at']},
            'provenance': {key: row[key] for key in ('body_path','body_sha256','archive_path','archive_sha256',
                'archive_paragraphs','retrieved_at','published_precision','text_mode','license')},
        }
    return sources


def load_scenario(name: str = 'smoke', directory: Path | str = DEFAULT_CORPUS) -> list[dict]:
    """Return source-only ordered intake rows, grouped by genuine reporting weeks.

    ``full`` is an alias for ``timeline``. Each row has phase, cohort, week_start,
    reference_time, source_id, discovered_at and a source ArticleInput mapping.
    Replay/resume are HTTP operations on these same rows, never extra news samples.
    """
    directory = Path(directory)
    name = 'timeline' if name == 'full' else name
    sources = load_sources(directory)
    rows = [row for row in read_csv(directory / 'scenarios.csv', SCENARIO_COLUMNS) if row['scenario'] == name]
    if not rows:
        raise ValueError(f'Unknown or empty corpus scenario: {name}')
    result = []
    for row in rows:
        if row['source_id'] not in sources:
            raise ValueError('Scenario references an unknown source')
        result.append({**sources[row['source_id']], **row, 'ordinal': int(row['ordinal'])})
    return result


def load_gold_events(directory: Path | str = DEFAULT_CORPUS) -> dict[str, dict]:
    """Evaluation only. Never put these annotations in source/provider contexts."""
    rows = read_csv(Path(directory) / 'gold_events.csv', GOLD_COLUMNS)
    if len({r['source_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate Event annotation')
    return {row['source_id']: row for row in rows}


def scenario_rows(name: str, rows: list[dict]) -> list[dict]:
    ordered = sorted(rows, key=lambda r: (r['published_at'], r['discovered_at'], r['source_id']))
    first_week = None
    result = []
    for index, row in enumerate(ordered):
        day = datetime.fromisoformat(row['published_at']).date()
        week = day - timedelta(days=day.weekday())
        first_week = first_week or week
        clock = datetime.combine(week + timedelta(days=7), time(hour=1), timezone.utc)
        result.append({'scenario': name, 'phase': 'initial' if week == first_week else 'incremental',
            'cohort': week.isoformat(), 'ordinal': index, 'source_id': row['source_id'],
            'week_start': week.isoformat(), 'reference_time': clock.isoformat(), 'expected_ingestion': 'new'})
    return result


def build_corpus(directory: Path | str = DEFAULT_CORPUS, dataset: Path | str = ORIGINAL_DATASET):
    from .validate_real_events import validate_dataset
    directory, dataset = Path(directory), Path(dataset)
    validate_dataset(dataset)
    data = json.loads(dataset.read_text(encoding='utf8'))
    archive_path = dataset.parent / data['source_archive']
    archive = json.loads(archive_path.read_text(encoding='utf8'))['sources']
    (directory/'sources').mkdir(parents=True, exist_ok=True)
    (directory/'archive').mkdir(parents=True, exist_ok=True)
    source_rows, gold_rows = [], []
    for sequence in data['sequences']:
        for index, row in enumerate(sequence['articles']):
            source, provenance, annotation = row['source'], row['provenance'], row['annotation']
            source_id = provenance['source_id']
            archived = archive[source_id]
            body_path = f'sources/{source_id}.txt'
            archived_path = f'archive/{source_id}.txt'
            (directory/body_path).write_bytes(source['body'].encode('utf8'))
            archive_bytes = '\n\n'.join(archived['paragraphs']).encode('utf8')
            (directory/archived_path).write_bytes(archive_bytes)
            source_rows.append({'source_id': source_id, 'stream': sequence['id'], 'sequence_index': index,
                'title': source['title'], 'url': source['url'], 'publisher': source['source'],
                'published_at': source['published_at'], 'discovered_at': row['discovered_at'],
                'published_precision': provenance['published_precision'], 'body_path': body_path,
                'body_sha256': digest(source['body'].encode()), 'archive_path': archived_path,
                'archive_sha256': digest(archive_bytes), 'archive_paragraphs': json.dumps(provenance['archive_paragraphs']),
                'retrieved_at': archived['retrieved_at'], 'text_mode': provenance['text_mode'], 'license': archived['license']})
            gold_rows.append({'source_id': source_id, **row['gold'], 'scope': annotation['scope'],
                **{key: annotation[key] for key in ('identity_confidence','state_confidence','annotator','review_status','rationale')},
                'verification_qualification': annotation['verification'], 'annotation_version': data['annotation_policy']['version']})
    write_csv(directory/'sources.csv', SOURCE_COLUMNS, source_rows)
    write_csv(directory/'gold_events.csv', GOLD_COLUMNS, gold_rows)
    write_csv(directory/'scenarios.csv', SCENARIO_COLUMNS,
              scenario_rows('smoke', [r for r in source_rows if r['source_id'] in SMOKE_IDS]) + scenario_rows('timeline', source_rows))
    write_csv(directory/'gold_workflow_smoke.csv', SMOKE_GOLD_COLUMNS, [
        dict(zip(SMOKE_GOLD_COLUMNS, (source_id,'readable official dated release',categories,'confirmed',
            fact,qualification,behavior,numeric,forbidden,brief,
            'single_assistant_qualitative_constraints_not_independently_adjudicated')))
        for source_id,categories,fact,qualification,behavior,numeric,forbidden,brief in SMOKE_GUIDANCE])
    write_csv(directory/'cases.csv', CASE_COLUMNS, [dict(zip(CASE_COLUMNS,row)) for row in CONTRACT_CASES])
    inventory = {path.relative_to(directory).as_posix(): digest(path.read_bytes())
                 for path in sorted(directory.rglob('*')) if path.is_file() and path.suffix in {'.csv', '.txt'}}
    manifest = {'schema_version': 1, 'name': 'real-workflow-txt-csv-v1', 'source_count': len(source_rows),
        'unique_urls': len({r['url'] for r in source_rows}), 'gold_event_count': len({r['event'] for r in gold_rows}),
        'original_dataset': dataset.relative_to(ROOT).as_posix() if dataset.is_relative_to(ROOT) else dataset.name,
        'original_dataset_sha256': digest(dataset.read_bytes().replace(b'\r\n', b'\n')), 'original_archive_sha256': digest(archive_path.read_bytes().replace(b'\r\n', b'\n')),
        'source_kind': 'authentic_official_news_selected_passages', 'files_sha256': inventory,
        'time_policy': 'Original publication dates and simulated discovery clocks are preserved; UTC reporting weeks; date-only publication encodes 00:00 UTC, not an asserted event time.',
        'annotation_policy': data['annotation_policy'], 'limitations': data['limitations'] + [
            'Full service runs generate fresh W2/W3/E/W4 output. Existing frozen W2 fields are deliberately not exported as model inputs.',
            'Existing E labels were frozen for two independent domain streams. The combined timeline introduces cross-domain competition; report this changed scope.',
            'No independent W1/W2/W3/W4 semantic accuracy gold is claimed. HTTP/stage continuity checks are separate from semantic accuracy.',
            'Replay and resumed jobs do not increase the number of unique authentic sources.'],
        'licensing_information': data['sources_license_urls']}
    (directory/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    return validate_corpus(directory, dataset=dataset)


def validate_corpus(directory: Path | str = DEFAULT_CORPUS, *, dataset: Path | str | None = ORIGINAL_DATASET):
    directory = Path(directory)
    manifest = json.loads((directory/'manifest.json').read_text(encoding='utf8'))
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported portable corpus version')
    expected_files = manifest['files_sha256']
    actual_files = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() and p.suffix in {'.csv','.txt'}}
    if set(expected_files) != actual_files:
        raise ValueError('Corpus inventory differs from manifest')
    for relative, expected in expected_files.items():
        if digest(safe_path(directory, relative).read_bytes()) != expected:
            raise ValueError(f'Corpus file hash mismatch: {relative}')
    sources = load_sources(directory)
    gold = load_gold_events(directory)
    smoke_gold = read_csv(directory/'gold_workflow_smoke.csv',SMOKE_GOLD_COLUMNS)
    if {row['source_id'] for row in smoke_gold} != set(SMOKE_IDS) or len(smoke_gold) != len(SMOKE_IDS):
        raise ValueError('Qualitative workflow guidance must cover exactly the four smoke sources')
    cases = read_csv(directory/'cases.csv',CASE_COLUMNS)
    if len({row['case_id'] for row in cases}) != len(cases):
        raise ValueError('Duplicate workflow contract case ID')
    if len(sources) != manifest['source_count'] or set(sources) != set(gold):
        raise ValueError('Corpus source/annotation coverage mismatch')
    if len({r['source']['url'] for r in sources.values()}) != len(sources):
        raise ValueError('Duplicate URLs cannot increase authentic sample count')
    for source_id, row in sources.items():
        provenance = row['provenance']
        archived = safe_path(directory, provenance['archive_path']).read_bytes()
        if digest(archived) != provenance['archive_sha256']:
            raise ValueError(f'Archived source hash mismatch: {source_id}')
    scenarios = {}
    for name in ('smoke','timeline'):
        rows = load_scenario(name, directory)
        if [r['ordinal'] for r in rows] != list(range(len(rows))) or len({r['source_id'] for r in rows}) != len(rows):
            raise ValueError('Scenario order or unique input coverage is invalid')
        prior_time = None
        for row in rows:
            published = datetime.fromisoformat(row['source']['published_at'])
            discovered = datetime.fromisoformat(row['discovered_at'])
            clock = datetime.fromisoformat(row['reference_time'])
            week = datetime.fromisoformat(row['week_start']).date()
            if not (week <= published.date() < week+timedelta(days=7) and published <= discovered <= clock):
                raise ValueError('Scenario has future or out-of-week source evidence')
            if clock.date() != week+timedelta(days=7) or row['cohort'] != week.isoformat():
                raise ValueError('Scenario reporting clock does not match the completed week')
            if prior_time is not None and published < prior_time:
                raise ValueError('Scenario time regressed')
            if row['phase'] != ('initial' if row['week_start'] == rows[0]['week_start'] else 'incremental'):
                raise ValueError('Unexpected initial/incremental phase')
            if row['expected_ingestion'] != 'new':
                raise ValueError('Replay must not be counted as newly collected news')
            prior_time = published
        scenarios[name] = {'sources': len(rows), 'cohorts': len({r['cohort'] for r in rows})}
    if {r['source_id'] for r in load_scenario('timeline', directory)} != set(sources):
        raise ValueError('Full timeline must cover every authentic source exactly once')
    if dataset is not None:
        dataset = Path(dataset)
        data = json.loads(dataset.read_text(encoding='utf8'))
        if digest(dataset.read_bytes().replace(b'\r\n', b'\n')) != manifest['original_dataset_sha256']:
            raise ValueError('Original dataset has changed; rebuild as a new audited corpus version')
        archived_path = dataset.parent/data['source_archive']
        if digest(archived_path.read_bytes().replace(b'\r\n', b'\n')) != manifest['original_archive_sha256']:
            raise ValueError('Original source archive has changed')
        archived_sources = json.loads(archived_path.read_text(encoding='utf8'))['sources']
        for sequence in data['sequences']:
            for index, original in enumerate(sequence['articles']):
                source_id = original['provenance']['source_id']
                if sources[source_id]['source'] != original['source'] or sources[source_id]['discovered_at'] != original['discovered_at']:
                    raise ValueError('Portable source differs from original frozen source')
                if any(gold[source_id][key] != value for key, value in original['gold'].items()):
                    raise ValueError('Portable Event labels differ from frozen original labels')
                if sources[source_id]['stream'] != sequence['id'] or sources[source_id]['sequence_index'] != index:
                    raise ValueError('Original domain stream or sequence order has changed')
                if any(gold[source_id][key] != original['annotation'][key] for key in
                       ('scope','identity_confidence','state_confidence','annotator','review_status','rationale')):
                    raise ValueError('Original annotation provenance has changed')
                archived = safe_path(directory,sources[source_id]['provenance']['archive_path']).read_bytes()
                if archived != '\n\n'.join(archived_sources[source_id]['paragraphs']).encode('utf8'):
                    raise ValueError('Portable source archive differs from original saved paragraphs')

    return {'status': 'passed', 'sources': len(sources), 'unique_urls': len(sources),
            'event_occurrences': len({r['event'] for r in gold.values()}), 'scenarios': scenarios,
            'body_roundtrip': 'exact_utf8', 'semantic_gold_scope': 'E only; existing single-annotator labels',
            'frozen_w2_model_inputs_exported': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_CORPUS)
    parser.add_argument('--rebuild', action='store_true', help='Reproduce TXT/CSV from the existing frozen real-news archive')
    args = parser.parse_args()
    result = build_corpus(args.directory) if args.rebuild else validate_corpus(args.directory)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
