"""Offline checks: source citation validation, API failures and baseline isolation."""
import copy
import unittest
from unittest.mock import patch

import app
import rag

EVIDENCE = [{'id': 'E1', 'section': '173', 'document': 'crpc-1898-part-2.pdf',
             'pdf_page': 99, 'printed_page': 137,
             'text': 'a copy of any report forwarded under this section shall on application, be furnished to the accused before the commencement of the inquiry or trial:'}]
RESULT = {'status': 'answer', 'answer': 'An accused can apply for a copy. [E1]',
          'references': ['E1']}


class GroundingTests(unittest.TestCase):
    def test_retrieval_failures_identify_database_embedding_and_data_problems(self):
        failures = [
            (rag.psycopg.OperationalError('private database detail'), 'Database connection or query failed'),
            (rag.embed.EmbeddingAPIError('Gemini embedding quota reached.'), 'Gemini embedding quota reached'),
            (rag.httpx.ConnectError('private transport detail'), 'Could not reach the Gemini'),
            (RuntimeError('private source detail'), 'Source or embedding data could not be read'),
        ]
        for error, expected in failures:
            with self.subTest(error=type(error).__name__), \
                    patch('rag.psycopg.connect'), patch('rag.httpx.Client'), \
                    patch('rag.embed.search', side_effect=error):
                with self.assertRaises(rag.RetrievalError) as caught:
                    rag.retrieve('Can I get a copy?', 'configured', 'configured')
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn('private', str(caught.exception))

    def test_embedding_api_failures_exclude_raw_provider_response(self):
        from unittest.mock import Mock
        for status in (401, 403, 429, 503):
            client = Mock()
            client.post.return_value = rag.httpx.Response(status, text='private provider details')
            with self.subTest(status=status), self.assertRaises(rag.embed.EmbeddingAPIError) as caught:
                rag.embed.api_post(client, 'batchEmbedContents', {})
            self.assertNotIn('private', str(caught.exception))

    def test_citations_use_source_metadata(self):
        answer, cites = rag.resolve_references(RESULT, EVIDENCE)
        self.assertEqual(answer, 'An accused can apply for a copy. [1]')
        self.assertEqual(cites[0]['pdf_pages'], [99])
        self.assertEqual(cites[0]['printed_pages'], [137])
        self.assertEqual(cites[0]['excerpt'], EVIDENCE[0]['text'])

    def test_backend_formats_valid_references_when_model_omits_markers(self):
        result = copy.deepcopy(RESULT)
        result['answer'] = 'An accused can apply for a copy.'
        answer, citations = rag.resolve_references(result, EVIDENCE)
        self.assertEqual(answer, 'An accused can apply for a copy. [1]')
        self.assertEqual(len(citations), 1)

    def test_unknown_ids_missing_references_and_invalid_format_rejected(self):
        for kind in ('id', 'empty', 'marker', 'object', 'not_list'):
            result = copy.deepcopy(RESULT)
            if kind == 'id': result['references'][0] = 'E99'
            if kind == 'empty': result['references'] = []
            if kind == 'marker': result['answer'] = 'Some claim. [E2]'
            if kind == 'object': result['references'] = [{'evidence_id': 'E1', 'quote': 'Invented text'}]
            if kind == 'not_list': result['references'] = 'E1'
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                rag.resolve_references(result, EVIDENCE)

    def test_paraphrase_and_duplicate_source_ids_are_accepted(self):
        result = {'status': 'answer', 'answer': 'Before trial or inquiry begins, the accused may request the report.',
                  'references': ['E1', 'E1']}
        answer, citations = rag.resolve_references(result, EVIDENCE)
        self.assertEqual(answer, result['answer'] + ' [1]')
        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]['excerpt'], EVIDENCE[0]['text'])

    def test_failed_validation_reason_is_recorded_for_evaluation(self):
        metadata = {}
        with patch.dict(app.app.config, {'GROQ_API_KEY': 'test'}), \
                patch('app.rag.retrieve', return_value=EVIDENCE), \
                patch('app.generate_answer', side_effect=ValueError('Unknown evidence ID')):
            body, status = app.answer_question('Can I obtain a copy?', grounded=True, metadata=metadata)
        self.assertEqual(status, 502)
        self.assertEqual(metadata['validation_error'], 'ValueError: Unknown evidence ID')
        self.assertNotIn('validation_error', body)

    def test_abstention_has_no_citations(self):
        result = {'status': 'insufficient_evidence', 'answer': 'The archive cannot verify this.', 'references': []}
        self.assertEqual(rag.resolve_references(result, EVIDENCE)[1], [])
        result['references'] = RESULT['references']
        with self.assertRaises(ValueError): rag.resolve_references(result, EVIDENCE)

    def test_api_retrieves_and_fails_closed(self):
        with patch.dict(app.app.config, {'GROQ_API_KEY': 'test'}), \
                patch('app.rag.retrieve', side_effect=rag.RetrievalError('Unavailable')), \
                patch('app.generate_answer') as generate:
            response = app.app.test_client().post('/ask', json={'question': 'What is the procedure?'})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json['error']['code'], 'retrieval_unavailable')
            generate.assert_not_called()

    def test_api_passes_retrieved_passages_and_rejects_invalid_citations(self):
        with patch.dict(app.app.config, {'GROQ_API_KEY': 'test'}), \
                patch('app.rag.retrieve', return_value=EVIDENCE), \
                patch('app.generate_answer', side_effect=ValueError('bad quote')) as generate:
            response = app.app.test_client().post('/ask', json={'question': 'Can I obtain a copy?'})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(generate.call_args.kwargs['evidence'], EVIDENCE)
            self.assertEqual(response.json['citations'], [])

    def test_baseline_does_not_retrieve(self):
        with patch.dict(app.app.config, {'GROQ_API_KEY': 'test'}), \
                patch('app.rag.retrieve') as retrieve, \
                patch('app.generate_answer', return_value={}) as generate:
            app.answer_question('What is the procedure?')
            retrieve.assert_not_called()
            self.assertIsNone(generate.call_args.kwargs['evidence'])

    def test_bad_input_does_not_call_providers(self):
        with patch('app.rag.retrieve') as retrieve:
            client = app.app.test_client()
            self.assertEqual(client.get('/health').status_code, 200)
            for body in ({}, {'question': ''}, {'question': 3}, {'question': 'x'*4001}, []):
                self.assertEqual(client.post('/ask', json=body).status_code, 400)
            retrieve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
