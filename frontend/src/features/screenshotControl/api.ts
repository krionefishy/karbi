import { apiDownload, apiRequest } from "../../api/http";
import type {
  AdminConfig,
  AdminQueuePage,
  AdminSubmissionDetail,
  ExtensionInstall,
  FileErase,
  ImageKind,
  ImageUrl,
  ManagerConfig,
  ManagerSettings,
  Managers,
  Outcome,
  PairingCode,
  Pipeline,
  QueueFilters,
  Reprocess,
  ResolveRequest,
  Submission,
  SubmissionFilters,
  SubmissionPage,
  Summary,
  WebSubmission,
} from "./types";

const root = "/api/v1/wb/screenshot-control";
const adminRoot = "/api/v1/admin/screenshot-control";

type Query = Record<string, string | number | undefined | null>;

function withQuery(path: string, query: Query = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const search = params.toString();
  return search ? `${path}?${search}` : path;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

// --- менеджер ---

export function uploadSubmission(submission: WebSubmission) {
  const body = new FormData();
  body.append("file", submission.file);
  body.append("client_submission_id", submission.clientSubmissionId);
  if (submission.outcome) body.append("outcome", submission.outcome);
  if (submission.clientCapturedAt) body.append("client_captured_at", submission.clientCapturedAt);
  return apiRequest<Submission>(`${root}/submissions`, { method: "POST", body });
}

export const getSubmissions = (filters: SubmissionFilters = {}) =>
  apiRequest<SubmissionPage>(withQuery(`${root}/submissions`, { ...filters }));

export const setOutcome = (submissionId: string, outcome: Outcome) =>
  apiRequest<Submission>(`${root}/submissions/${submissionId}/outcome`, json("PATCH", { outcome }));

export const getSummary = (period: { from?: string; to?: string } = {}) =>
  apiRequest<Summary>(withQuery(`${root}/summary`, period));

export const getConfig = () => apiRequest<ManagerConfig>(`${root}/config`);

export const getSettings = () => apiRequest<ManagerSettings>(`${root}/settings`);

export const updateSettings = (timezone: string) =>
  apiRequest<ManagerSettings>(`${root}/settings`, json("PUT", { timezone }));

export const createPairingCode = () =>
  apiRequest<PairingCode>(`${root}/extension/pairing-code`, { method: "POST" });

export const getInstalls = () => apiRequest<ExtensionInstall[]>(`${root}/extension/installs`);

export const revokeInstall = (installId: string) =>
  apiRequest<void>(`${root}/extension/installs/${installId}`, { method: "DELETE" });

// --- админ ---

export const getQueue = (filters: QueueFilters = {}) =>
  apiRequest<AdminQueuePage>(withQuery(`${adminRoot}/queue`, { ...filters }));

export const getAdminSubmission = (submissionId: string) =>
  apiRequest<AdminSubmissionDetail>(`${adminRoot}/submissions/${submissionId}`);

export const getAdminImageUrl = (submissionId: string, kind: ImageKind = "thumb") =>
  apiRequest<ImageUrl>(withQuery(`${adminRoot}/submissions/${submissionId}/image-url`, { kind }));

export const resolveSubmission = (submissionId: string, payload: ResolveRequest) =>
  apiRequest<AdminSubmissionDetail>(`${adminRoot}/submissions/${submissionId}/resolve`, json("POST", payload));

export const reprocessSubmission = (submissionId: string) =>
  apiRequest<Reprocess>(`${adminRoot}/submissions/${submissionId}/reprocess`, { method: "POST" });

export const eraseSubmissionFile = (submissionId: string) =>
  apiRequest<FileErase>(`${adminRoot}/submissions/${submissionId}/file`, { method: "DELETE" });

export const getManagers = (period: { from?: string; to?: string } = {}) =>
  apiRequest<Managers>(withQuery(`${adminRoot}/managers`, period));

/** CSV для HR/KPI: менеджер × день, с флагом preliminary по каждому дню (см. ManagerDayRow). */
export const downloadManagersExport = (period: { from?: string; to?: string } = {}) =>
  apiDownload(withQuery(`${adminRoot}/managers/export`, period));

export const getPipeline = () => apiRequest<Pipeline>(`${adminRoot}/pipeline`);

export const getAdminConfig = () => apiRequest<AdminConfig>(`${adminRoot}/config`);
