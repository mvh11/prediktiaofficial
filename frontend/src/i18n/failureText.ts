import type { FailureInfo } from '../api/describeError.ts';
import type { Messages } from './messages.es-CL.ts';

export interface FailureText {
  readonly title: string;
  readonly paragraphs: readonly string[];
}

/** Turns a language-agnostic failure into localized copy. */
export function failureText(failure: FailureInfo, subject: string, m: Messages): FailureText {
  const e = m.errors;
  switch (failure.category) {
    case 'not_configured':
      return { title: e.notConfiguredTitle, paragraphs: [e.notConfigured(subject)] };
    case 'offline':
      return { title: e.offlineTitle, paragraphs: [e.offline(subject)] };
    case 'unavailable':
      return {
        title: e.unavailableTitle,
        paragraphs: [failure.status === null ? e.unavailable(subject) : e.unavailableStatus(subject, failure.status)],
      };
    case 'timeout':
      return { title: e.timeoutTitle, paragraphs: [e.timeout(subject, Math.round((failure.timeoutMs ?? 0) / 1000))] };
    case 'not_found':
      return { title: e.notFoundTitle, paragraphs: [e.notFound(subject)] };
    case 'rejected': {
      const status = failure.status ?? 0;
      return { title: status >= 500 ? e.serverErrorTitle : e.rejectedTitle, paragraphs: [e.rejected(subject, status)] };
    }
    case 'unexpected':
      return { title: e.unexpectedTitle, paragraphs: [e.unexpected(subject)] };
    case 'invalid': {
      const invalid = failure.invalid;
      if (invalid?.reason === 'unsafe_integer' && invalid.literal !== undefined) {
        return { title: e.unsafeIntegerTitle, paragraphs: [e.unsafeInteger(invalid.literal)] };
      }
      if (invalid?.reason === 'invalid_identifier' && invalid.literal !== undefined) {
        const paragraphs = [e.invalidIdentifier(invalid.literal, invalid.field ?? 'id')];
        if (invalid.row !== undefined) paragraphs.push(e.invalidIdentifierRow(invalid.row), e.invalidOneRow);
        return { title: e.invalidIdentifierTitle, paragraphs };
      }
      if (invalid?.reason === 'identity_mismatch' && invalid.literal !== undefined && invalid.expected !== undefined) {
        return { title: e.identityMismatchTitle, paragraphs: [e.identityMismatch(invalid.expected, invalid.literal)] };
      }
      const paragraphs = [e.invalid(subject)];
      if (invalid?.reason === 'not_json') paragraphs.push(e.invalidNotJson);
      else if (invalid?.reason === 'malformed_json') paragraphs.push(e.invalidMalformedJson);
      else if (invalid?.reason === 'schema') {
        if (invalid.row !== undefined) {
          const field = invalid.field ?? m.errors.wholeRecord;
          paragraphs.push(invalid.rowId !== undefined ? e.invalidRowWithId(invalid.row, invalid.rowId, field) : e.invalidRow(invalid.row, field));
          paragraphs.push(e.invalidOneRow);
        } else if (invalid.field !== undefined) {
          paragraphs.push(e.invalidField(invalid.field));
        }
      }
      return { title: e.invalidTitle, paragraphs };
    }
  }
}
