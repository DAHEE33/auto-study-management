/**
 * Member_Master 예치금 변경 이력 자동 적재 스크립트.
 *
 * - Member_Master 시트의 "예치금" 컬럼이 수정되면 Deposit_History에 자동 기록
 * - 닉네임/행 순서 변경과 무관하게 UserKey 기준으로 추적
 * - 스터디 종료 후 Member_Master 행 삭제 여부와 무관하게 히스토리는 유지
 */
const SETTINGS = {
  MEMBER_SHEET: 'Member_Master',
  HISTORY_SHEET: 'Deposit_History',
  HEADER_ROW: 1,
  REQUIRED_MEMBER_HEADERS: ['닉네임', 'UserKey', '예치금'],
  HISTORY_HEADERS: [
    '기록시각',
    'UserKey',
    '닉네임_snapshot',
    '변경전예치금',
    '변경후예치금',
    '증감액',
    '이벤트유형',
    '수정셀(A1)',
    '수정자',
    '메모',
  ],
};

function onEdit(e) {
  if (!e || !e.range || !e.source) return;

  const range = e.range;
  const sheet = range.getSheet();
  if (sheet.getName() !== SETTINGS.MEMBER_SHEET) return;

  // 여러 셀 동시 편집은 이력 왜곡 가능성이 있어 무시
  if (range.getNumRows() !== 1 || range.getNumColumns() !== 1) return;

  const row = range.getRow();
  if (row <= SETTINGS.HEADER_ROW) return;

  const headerMap = getHeaderMap_(sheet, SETTINGS.HEADER_ROW);
  if (!hasRequiredHeaders_(headerMap, SETTINGS.REQUIRED_MEMBER_HEADERS)) return;

  const depositCol = headerMap['예치금'];
  if (range.getColumn() !== depositCol) return;

  const oldDeposit = parseAmount_(e.oldValue);
  const newDeposit = parseAmount_(e.value);

  // 숫자가 아니거나 변경 없음이면 무시
  if (newDeposit === null) return;
  if (oldDeposit !== null && oldDeposit === newDeposit) return;

  const nickname = String(sheet.getRange(row, headerMap['닉네임']).getDisplayValue() || '').trim();
  const userKey = String(sheet.getRange(row, headerMap['UserKey']).getDisplayValue() || '').trim();
  const delta = oldDeposit === null ? '' : newDeposit - oldDeposit;

  const eventType = classifyEvent_(oldDeposit, newDeposit);
  const editor = Session.getActiveUser().getEmail() || 'unknown';

  const historySheet = ensureHistorySheet_(e.source);
  historySheet.appendRow([
    new Date(),
    userKey,
    nickname,
    oldDeposit === null ? '' : oldDeposit,
    newDeposit,
    delta,
    eventType,
    range.getA1Notation(),
    editor,
    oldDeposit === null ? 'oldValue 미수신(복붙/수식 변경 가능)' : '자동기록',
  ]);
}

function getHeaderMap_(sheet, headerRow) {
  const headers = sheet
    .getRange(headerRow, 1, 1, sheet.getLastColumn())
    .getDisplayValues()[0]
    .map((h) => String(h || '').trim());

  const map = {};
  headers.forEach((header, idx) => {
    if (header) map[header] = idx + 1;
  });
  return map;
}

function hasRequiredHeaders_(headerMap, requiredHeaders) {
  return requiredHeaders.every((key) => !!headerMap[key]);
}

function parseAmount_(raw) {
  if (raw === undefined || raw === null || raw === '') return null;
  const normalized = String(raw).replace(/,/g, '').trim();
  if (!/^-?\d+(\.\d+)?$/.test(normalized)) return null;
  return Number(normalized);
}

function classifyEvent_(oldDeposit, newDeposit) {
  if (oldDeposit === null) return 'UNKNOWN_OLDVALUE';
  if (newDeposit === 10000 && oldDeposit > 10000) return 'PRIZE_PAYOUT_RESET';
  if (newDeposit > oldDeposit) return 'DEPOSIT_INCREASE';
  if (newDeposit < oldDeposit) return 'DEPOSIT_DECREASE';
  return 'NO_CHANGE';
}

function ensureHistorySheet_(spreadsheet) {
  let history = spreadsheet.getSheetByName(SETTINGS.HISTORY_SHEET);
  if (!history) {
    history = spreadsheet.insertSheet(SETTINGS.HISTORY_SHEET);
    history.getRange(1, 1, 1, SETTINGS.HISTORY_HEADERS.length).setValues([SETTINGS.HISTORY_HEADERS]);
  } else if (history.getLastRow() === 0) {
    history.getRange(1, 1, 1, SETTINGS.HISTORY_HEADERS.length).setValues([SETTINGS.HISTORY_HEADERS]);
  }
  return history;
}
