// Типы в types.ts написаны руками, а здесь они сверяются с контрактом бэкенда: schema.d.ts
// сгенерирован из схемы OpenAPI (`just screenshot-types`). Если бэкенд поменяет форму ответа,
// упадёт `npm run lint`, а не прод. Файл только из типов, в сборку ничего не попадает.
// Три расхождения оставлены намеренно и описаны по месту.

import type { components } from "./schema";
import type * as T from "./types";
import type { StorageState } from "./types";

type S = components["schemas"];

type Equal<A, B> = (<X>() => X extends A ? 1 : 2) extends (<X>() => X extends B ? 1 : 2) ? true : false;
// Пересечения и интерфейсы Equal считает разными типами, поэтому обе стороны приводятся к плоскому виду.
type Flat<X> = { [K in keyof X]: X[K] };
type Same<A, B> = Equal<Flat<A>, Flat<B>>;
type Expect<X extends true> = X;


export type _ErrorResponse = Expect<Same<T.ErrorResponse, S["ErrorResponse"]>>;
export type _Period = Expect<Same<T.Period, S["Period"]>>;
export type _SubmissionFields = Expect<Same<T.SubmissionFields, S["SubmissionFields"]>>;
export type _Submission = Expect<Same<T.Submission, S["SubmissionOut"]>>;
export type _SubmissionPage = Expect<Same<T.SubmissionPage, S["SubmissionPage"]>>;
export type _OutcomeCounts = Expect<Same<T.OutcomeCounts, S["OutcomeCounts"]>>;
export type _Summary = Expect<Same<T.Summary, S["Summary"]>>;
export type _ImageUrl = Expect<Same<T.ImageUrl, S["ImageUrlOut"]>>;
export type _OutcomeOption = Expect<Same<T.OutcomeOption, S["OutcomeOption"]>>;
export type _ManagerConfig = Expect<Same<T.ManagerConfig, S["ManagerConfigOut"]>>;
export type _Zone = Expect<Same<T.Zone, S["ZoneOut"]>>;
export type _ManagerSettings = Expect<Same<T.ManagerSettings, S["ManagerSettingsOut"]>>;
export type _PairingCode = Expect<Same<T.PairingCode, S["PairingCodeOut"]>>;
export type _ExtensionInstall = Expect<Same<T.ExtensionInstall, S["ExtensionInstallOut"]>>;
export type _TelegramLink = Expect<Same<T.TelegramLink, S["TelegramLinkOut"]>>;
export type _TelegramStatus = Expect<Same<T.TelegramStatus, S["TelegramStatusOut"]>>;
export type _ExtensionPair = Expect<Same<T.ExtensionPair, S["ExtensionPairOut"]>>;
export type _ExtensionMe = Expect<Same<T.ExtensionMe, S["ExtensionMeOut"]>>;
export type _ExtensionSubmissionList = Expect<Same<T.ExtensionSubmissionList, S["ExtensionSubmissionList"]>>;
export type _UserRef = Expect<Same<T.UserRef, S["UserRef"]>>;
export type _Reason = Expect<Same<T.Reason, S["ReasonOut"]>>;
export type _Match = Expect<Same<T.Match, S["MatchOut"]>>;
export type _FieldConfidence = Expect<Same<T.FieldConfidence, S["FieldConfidence"]>>;
export type _QualityFlags = Expect<Same<T.QualityFlags, S["QualityFlags"]>>;
export type _ClientMessage = Expect<Same<T.ClientMessage, S["ClientMessage"]>>;
export type _AdminFields = Expect<Same<T.AdminFields, S["AdminFields"]>>;
export type _AdminSubmission = Expect<Same<T.AdminSubmission, S["AdminSubmission"]>>;
export type _ClientMeta = Expect<Same<T.ClientMeta, S["ClientMeta"]>>;
export type _FileMeta = Expect<Same<T.FileMeta, S["FileMeta"]>>;
export type _StorageObject = Expect<Same<T.StorageObject, S["StorageObjectOut"]>>;
export type _FileInfo = Expect<Same<T.FileInfo, S["FileOut"]>>;
export type _Extraction = Expect<Same<T.Extraction, S["ExtractionOut"]>>;
export type _Actor = Expect<Same<T.Actor, S["ActorOut"]>>;
export type _VerdictEvent = Expect<Same<T.VerdictEvent, S["VerdictEventOut"]>>;
export type _AdminSubmissionDetail = Expect<Same<T.AdminSubmissionDetail, S["AdminSubmissionDetail"]>>;
export type _QueueCounts = Expect<Same<T.QueueCounts, S["QueueCounts"]>>;
export type _AdminQueuePage = Expect<Same<T.AdminQueuePage, S["AdminQueuePage"]>>;
export type _Reprocess = Expect<Same<T.Reprocess, S["ReprocessOut"]>>;
export type _FileErase = Expect<Same<T.FileErase, S["FileEraseOut"]>>;
export type _ManagerStats = Expect<Same<T.ManagerStats, S["ManagerStats"]>>;
export type _Managers = Expect<Same<T.Managers, S["ManagersOut"]>>;
export type _Thresholds = Expect<Same<T.Thresholds, S["ThresholdsOut"]>>;
export type _AdminConfig = Expect<Same<T.AdminConfig, S["AdminConfigOut"]>>;
export type _AdminTelegramLink = Expect<Same<T.AdminTelegramLink, S["AdminTelegramLinkOut"]>>;

// browser и note на бэкенде со значением по умолчанию, поэтому в схеме они обязательные,
// а клиенту их разрешено не присылать: сверяем остальные поля и тип самого поля.
export type _ExtensionPairRequest = Expect<
  Same<Omit<T.ExtensionPairRequest, "browser">, Omit<S["ExtensionPairRequest"], "browser">>
>;
export type _ExtensionPairRequestBrowser = Expect<
  Same<NonNullable<T.ExtensionPairRequest["browser"]>, S["ExtensionPairRequest"]["browser"]>
>;
export type _ResolveRequest = Expect<Same<Omit<T.ResolveRequest, "note">, Omit<S["ResolveRequest"], "note">>>;
export type _ResolveRequestNote = Expect<
  Same<NonNullable<T.ResolveRequest["note"]>, S["ResolveRequest"]["note"]>
>;

// В storage.objects фронт сужает ключи до StorageState, схема описывает их как любые строки.
export type _Pipeline = Expect<Same<Omit<T.Pipeline, "storage">, Omit<S["PipelineOut"], "storage">>>;
export type _PipelineStorage = Expect<
  Same<Omit<T.Pipeline["storage"], "objects">, Omit<S["StorageStats"], "objects">>
>;
export type _PipelineStorageObjects = Expect<
  Same<T.Pipeline["storage"]["objects"], Partial<Record<StorageState, number>>>
>;
