{{/*
Expand the name of the chart.
*/}}
{{- define "reproserver.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "reproserver.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "reproserver.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "reproserver.labels" -}}
helm.sh/chart: {{ include "reproserver.chart" . }}
{{ include "reproserver.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "reproserver.selectorLabels" -}}
app.kubernetes.io/name: {{ include "reproserver.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "reproserver.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "reproserver.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create the name of the role to create for the service account
*/}}
{{- define "reproserver.roleName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "reproserver.fullname" .) .Values.serviceAccount.roleName }}
{{- else }}
{{- default "default" .Values.serviceAccount.roleName }}
{{- end }}
{{- end }}

{{/*
The name of the minio secret
*/}}
{{- define "reproserver.minioSecretName" -}}
{{- $minioSecretName := get (.Values.minio.secret | default dict) "name" -}}
{{- if not (empty $minioSecretName) -}}
  {{- $minioSecretName -}}
{{- else -}}
  {{- if .Values.minio.enabled -}}
    {{- include "minio.fullname" .Subcharts.minio -}}
  {{- else -}}
    {{- include "reproserver.fullname" . -}}-minio
  {{- end -}}
{{- end -}}
{{- end -}}

{{/*
The name of the postgres secret
*/}}
{{- define "reproserver.postgresSecretName" -}}
{{- $postgresSecretName := get (.Values.postgres.secret | default dict) "name" -}}
{{- if not (empty $postgresSecretName) -}}
  {{- $postgresSecretName -}}
{{- else -}}
  {{- if .Values.postgres.enabled -}}
    {{- include "postgres.fullname" .Subcharts.postgres -}}
  {{- else -}}
    {{- include "reproserver.fullname" . -}}-postgres
  {{- end -}}
{{- end -}}
{{- end -}}

{{/*
The name of the minio service
*/}}
{{- define "reproserver.minioServiceName" -}}
{{- if .Values.minio.enabled -}}
{{- include "minio.fullname" .Subcharts.minio -}}
{{- else -}}
{{- .Values.minio.serviceName -}}
{{- end -}}
{{- end -}}

{{/*
The name of the postgres service
*/}}
{{- define "reproserver.postgresServiceName" -}}
{{- if .Values.postgres.enabled -}}
{{- include "postgres.fullname" .Subcharts.postgres -}}
{{- else -}}
{{- .Values.postgres.serviceName -}}
{{- end -}}
{{- end -}}

{{/*
The name of the registry service
*/}}
{{- define "reproserver.registryServiceName" -}}
{{- if .Values.registry.enabled -}}
{{- include "registry.fullname" .Subcharts.registry -}}
{{- else -}}
{{- .Values.registry.serviceName -}}
{{- end -}}
{{- end -}}
