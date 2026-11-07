"""TXT/CSV corpus provenance, temporal intake and separation from model inputs."""
import csv
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.real_corpus import (build_corpus, validate_corpus, load_sources, load_scenario,
                                   load_gold_events, DEFAULT_CORPUS, SOURCE_COLUMNS)


class CorpusTests(unittest.TestCase):
    def test_checked_in_corpus_is_exact_and_all_sources_are_temporal(self):
        report=validate_corpus()
        self.assertEqual(report['sources'],72)
        self.assertEqual(report['scenarios']['smoke'],{'sources':4,'cohorts':2})
        self.assertEqual(report['scenarios']['timeline']['cohorts'],57)
        self.assertEqual(len(load_scenario('full')),72)
        for row in load_scenario('smoke'):
            self.assertEqual(set(row['source']),{'title','body','url','source','published_at'})
            self.assertNotIn('gold',row)
            self.assertNotIn('w2',row)
        self.assertEqual(set(load_gold_events()),set(load_sources()))

    def test_rebuild_is_byte_reproducible(self):
        with TemporaryDirectory() as temporary:
            destination=Path(temporary)/'corpus'
            build_corpus(destination)
            expected=json.loads((DEFAULT_CORPUS/'manifest.json').read_text())
            actual=json.loads((destination/'manifest.json').read_text())
            self.assertEqual(actual,expected)
            for relative in actual['files_sha256']:
                self.assertEqual((destination/relative).read_bytes(),(DEFAULT_CORPUS/relative).read_bytes())
            build_corpus(destination)
            self.assertEqual(json.loads((destination/'manifest.json').read_text()),actual)

    def test_corruption_and_unlisted_inputs_do_not_pass(self):
        with TemporaryDirectory() as temporary:
            directory=Path(temporary)
            build_corpus(directory)
            body=next((directory/'sources').glob('*.txt'))
            body.write_bytes(body.read_bytes()+b' fabricated addition')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                validate_corpus(directory)
            build_corpus(directory)
            (directory/'sources'/'unexpected.txt').write_text('extra sample')
            with self.assertRaisesRegex(ValueError,'inventory'):
                validate_corpus(directory)

    def test_source_loader_rejects_escape_paths_without_reading_gold(self):
        with TemporaryDirectory() as temporary:
            directory=Path(temporary)/'corpus'
            build_corpus(directory)
            with (directory/'sources.csv').open(newline='') as handle:
                rows=list(csv.DictReader(handle))
            rows[0]['body_path']='../outside.txt'
            with (directory/'sources.csv').open('w',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=SOURCE_COLUMNS)
                writer.writeheader();writer.writerows(rows)
            with self.assertRaisesRegex(ValueError,'inside the corpus'):
                load_sources(directory)
            with self.assertRaisesRegex(ValueError,'Unknown or empty'):
                load_scenario('not-a-scenario',DEFAULT_CORPUS)


if __name__=='__main__':unittest.main()
