/** UI copy is English-only; there is no locale switching or secondary language. */
const STRINGS = {
  dashboard: "Dashboard",
  history: "History",
  signedInAs: "Signed in as",
  signOut: "Sign out",
  navRepos: "Repos",
  shellHome: "GitReader home",
  shellNav: "Main",
  shellCurrentRepo: "Current repository: {repo}",
  userMenu: "Account",
  unknownUser: "Unknown user",

  dashboardTitle: "Which repo would you like to understand?",
  retry: "Retry",

  historyTitle: "History",
  tasks: "Tasks",
  noHistory: "No history yet",
  deleteFailed: "Delete failed",
  cancelTaskFailed: "Failed to cancel task",

  chatHistory: "Chat History",
  noChatHistory: "No conversations yet",
  startChatHint: "Conversations will be saved after you start",
  deleteConfirm: "Delete failed, please retry",
  chatDefault: "Chat",
  loadingChat: "Loading conversation...",
  agentDeepAnalysis: "Agent Deep Analysis",
  agentDesc:
    "I will deeply analyze code structure, trace call chains, and provide comprehensive code understanding.",

  thinkingStep: "Thinking (step {n}/{max})...",
  agentNoResult: "Agent returned no result",
  agentWorking: "Agent is working",
  processing: "Processing",
  generatingPreview: "Generating answer...",
  deleteConfirmDialog: "Delete this conversation? This cannot be undone.",

  taskLoadingStamp: "loading",
  taskLoadingTitle: "Loading task status",
  taskInFlightTitle: "Generating the wiki",
  taskWaiting: "Waiting for the pipeline to start",
  taskProgress: "Generation progress",
  taskFailedTitle: "Generation failed",
  taskFailedNoDetail:
    "The backend reported a failure without an error message.",
  taskStoppedTitle: "This task did not finish",
  taskNoArtifactsTitle: "The wiki was not published",
  taskNoArtifactsDetail:
    "The task finished, but it has no structure or content URLs to read from.",
  taskNotFoundTitle: "Task not found",
  taskNotFoundDetail:
    "No task exists with this id. It may have been deleted, or the link is wrong.",
  taskUnreachableStamp: "offline",
  taskUnreachableTitle: "Could not reach the task service",
  taskRegenerate: "Generate again",
  taskRegenerating: "Starting…",
  taskBackToRepos: "Back to repos",
  taskIdLabel: "Task",
  taskCreatedLabel: "Started",
  taskUpdatedLabel: "Last update",
  wikiContents: "Contents",
  wikiCollapseToc: "Collapse contents",
  wikiExpandToc: "Expand contents",
  wikiToggleChapter: "Show or hide {title}",
  wikiOpenContents: "Open contents",
  wikiCloseContents: "Close contents",
  wikiStructureError: "Could not load the wiki contents",
  wikiPageError: "Could not load this page",
  wikiPageMissing: "This page has no published content.",
  wikiDiagram: "Diagram",
  loading: "Loading...",
  error: "Something went wrong",
} as const;

export type DictKey = keyof typeof STRINGS;

export const t = (
  key: DictKey,
  vars?: Record<string, string | number>,
): string => {
  let text: string = STRINGS[key] ?? String(key);
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      text = text.replace(`{${k}}`, String(v));
    }
  }
  return text;
};

export default STRINGS;
