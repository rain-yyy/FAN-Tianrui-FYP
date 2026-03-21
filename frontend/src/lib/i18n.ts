/** UI copy is English-only; there is no locale switching or secondary language. */
const STRINGS = {
  dashboard: 'Dashboard',
  history: 'History',
  signedInAs: 'Signed in as',
  signOut: 'Sign out',
  footerText: 'Project Wiki Generator · Powered by AI',

  apiOnline: 'API SYSTEM: ONLINE',
  apiOffline: 'API SYSTEM: OFFLINE',
  dashboardTitle: 'Which repo would you like to understand?',
  inputPlaceholder: 'Paste a GitHub repository URL...',
  generateButton: 'Generate',
  generating: 'Generating...',
  taskFailed: 'Task failed',
  retry: 'Retry',

  historyTitle: 'History',
  tasks: 'Tasks',
  noHistory: 'No history yet',
  deleteTask: 'Delete task',
  deleteFailed: 'Delete failed',
  cancelTaskFailed: 'Failed to cancel task',

  chatHistory: 'Chat History',
  noChatHistory: 'No conversations yet',
  startChatHint: 'Conversations will be saved after you start',
  newChat: 'New Chat',
  askPlaceholder: 'Type your question...',
  deleteConfirm: 'Delete failed, please retry',
  noSources: 'No sources',
  sources: 'Sources',
  chatDefault: 'Chat',
  loadingChat: 'Loading conversation...',
  agentDeepAnalysis: 'Agent Deep Analysis',
  quickQA: 'Quick Q&A',
  agentDesc:
    'I will deeply analyze code structure, trace call chains, and provide comprehensive code understanding.',
  ragDesc: 'Quickly search docs and code to answer your question precisely.',

  analyzingIntent: 'Analyzing intent',
  analyzingIntentDesc: 'Understanding your question and planning exploration...',
  analyzingStatus: 'Analyzing intent...',
  retrievingDocs: 'Retrieving relevant documents...',
  analysisDone: 'Analysis complete',
  intentLabel: 'Intent',
  keyEntities: 'Key entities',
  understandingQuestion: 'Understanding your question...',
  retrievalTitle: 'Retrieving documents',
  retrievalDesc: 'Searching knowledge base...',
  retrievalQuery: 'Search query',
  foundDocs: 'Found {n} relevant documents',
  generatingAnswer: 'Generating answer',
  generatingAnswerDesc: 'Writing answer based on search results...',
  answerDone: 'Answer generated',
  evaluatingInfo: 'Evaluating information sufficiency',
  evaluatingDesc: 'Checking if enough evidence has been collected...',
  infoSufficient: 'Information sufficient',
  needMoreInfo: 'Need more information',
  confidenceLabel: 'Confidence',
  missingItems: 'Missing {n} items',
  synthesisingAnswer: 'Generating answer',
  synthesisingDesc: 'Integrating evidence and composing answer...',
  agentNoResult: 'Agent returned no result',
  agentWorking: 'Agent is working',
  processing: 'Processing',
  generatingPreview: 'Generating answer...',
  hydeTitle: 'HyDE Document Generated',
  hydeDesc: 'Enhanced retrieval with hypothetical document',
  deleteConfirmDialog: 'Delete this conversation? This cannot be undone.',

  loading: 'Loading...',
  error: 'Something went wrong',
  notFoundIndex: 'No vector index found. Please generate docs first.',
} as const;

export type DictKey = keyof typeof STRINGS;

export const t = (key: DictKey, vars?: Record<string, string | number>): string => {
  let text: string = STRINGS[key] ?? String(key);
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      text = text.replace(`{${k}}`, String(v));
    }
  }
  return text;
};

export default STRINGS;
