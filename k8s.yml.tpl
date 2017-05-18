apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: reproserver-web-{{ tier }}
spec:
  replicas: 1
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: reproserver
        repro-pod: web
        tier: {{ tier }}
    spec:
      containers:
      - name: web
        image: reproserver-web{{ tag }}
        imagePullPolicy: Never
        env:
        - name: AMQP_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: AMQP_PASSWORD
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: AMQP_HOST
          value: reproserver-rabbitmq-{{ tier }}
        - name: S3_KEY
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: S3_SECRET
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: S3_URL
          value: "http://reproserver-minio-{{ tier }}:9000"
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: POSTGRES_HOST
          value: reproserver-postgres-{{ tier }}
        - name: POSTGRES_DB
          value: reproserver
        - name: WEB_BEHIND_PROXY
          value: "1"
        ports:
        - containerPort: 8000
        livenessProbe:
          httpGet:
            path: /
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
---
apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: reproserver-builder-{{ tier }}
spec:
  replicas: 1
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: reproserver
        repro-pod: builder
        tier: {{ tier }}
    spec:
      containers:
      - name: builder
        image: reproserver-builder{{ tag }}
        imagePullPolicy: Never
        env:
        - name: REPROZIP_USAGE_STATS
          value: "off"
        - name: REGISTRY
          value: "reproserver-registry-{{ tier }}:5000"
        - name: DOCKER_HOST
          value: 127.0.0.1:2375
        - name: AMQP_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: AMQP_PASSWORD
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: AMQP_HOST
          value: reproserver-rabbitmq-{{ tier }}
        - name: S3_KEY
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: S3_SECRET
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: S3_URL
          value: "http://reproserver-minio-{{ tier }}:9000"
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: POSTGRES_HOST
          value: reproserver-postgres-{{ tier }}
        - name: POSTGRES_DB
          value: reproserver
      - name: docker
        image: docker:dind
        securityContext:
          privileged: true
        args:
        - "--storage-driver=aufs"
        - "--insecure-registry=reproserver-registry-{{ tier }}:5000"
---
apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: reproserver-runner-{{ tier }}
spec:
  replicas: 1
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: reproserver
        repro-pod: runner
        tier: {{ tier }}
    spec:
      containers:
      - name: runner
        image: reproserver-runner{{ tag }}
        imagePullPolicy: Never
        env:
        - name: REGISTRY
          value: "reproserver-registry-{{ tier }}:5000"
        - name: DOCKER_HOST
          value: 127.0.0.1:2375
        - name: AMQP_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: AMQP_PASSWORD
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: AMQP_HOST
          value: reproserver-rabbitmq-{{ tier }}
        - name: S3_KEY
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: S3_SECRET
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: S3_URL
          value: "http://reproserver-minio-{{ tier }}:9000"
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        - name: POSTGRES_HOST
          value: reproserver-postgres-{{ tier }}
        - name: POSTGRES_DB
          value: reproserver
      - name: docker
        image: docker:dind
        securityContext:
          privileged: true
        args:
        - "--storage-driver=aufs"
        - "--insecure-registry=reproserver-registry-{{ tier }}:5000"
---
apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: reproserver-rabbitmq-{{ tier }}
spec:
  replicas: 1
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app: reproserver
        repro-pod: rabbitmq
        tier: {{ tier }}
    spec:
      containers:
      - name: rabbitmq
        image: rabbitmq:3.6.9-management
        env:
        - name: RABBITMQ_DEFAULT_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: RABBITMQ_DEFAULT_PASS
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        ports:
        - containerPort: 5672
        - containerPort: 8080
---
apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: reproserver-minio-{{ tier }}
spec:
  replicas: 1
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app: reproserver
        repro-pod: minio
        tier: {{ tier }}
    spec:
      containers:
      - name: minio
        image: "minio/minio:RELEASE.2017-04-29T00-40-27Z"
        args: ["server", "/export"]
        env:
        - name: MINIO_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: MINIO_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        ports:
        - containerPort: 9000
---
apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: reproserver-registry-{{ tier }}
spec:
  replicas: 1
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app: reproserver
        repro-pod: registry
        tier: {{ tier }}
    spec:
      containers:
      - name: registry
        image: registry:2.6
        ports:
        - containerPort: 5000
---
apiVersion: extensions/v1beta1
kind: Deployment
metadata:
  name: reproserver-postgres-{{ tier }}
spec:
  replicas: 1
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        app: reproserver
        repro-pod: postgres
        tier: {{ tier }}
    spec:
      containers:
      - name: postgres
        image: postgres:9.6
        env:
        - name: PGDATA
          value: /var/lib/postgresql/data/pgdata}
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: user
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: reproserver-secret-{{ tier }}
              key: password
        ports:
        - containerPort: 5432
---
apiVersion: v1
kind: Service
metadata:
  name: reproserver-rabbitmq-{{ tier }}
  labels:
    app: reproserver
    tier: {{ tier }}
spec:
  selector:
    app: reproserver
    repro-pod: rabbitmq
    tier: {{ tier }}
  ports:
  - protocol: TCP
    port: 5672
---
apiVersion: v1
kind: Service
metadata:
  name: reproserver-rabbitmq-management-{{ tier }}
  labels:
    app: reproserver
    tier: {{ tier }}
spec:
  selector:
    app: reproserver
    repro-pod: rabbitmq
    tier: {{ tier }}
  ports:
  - protocol: TCP
    port: 15672
---
apiVersion: v1
kind: Service
metadata:
  name: reproserver-minio-{{ tier }}
  labels:
    app: reproserver
    tier: {{ tier }}
spec:
  selector:
    app: reproserver
    repro-pod: minio
    tier: {{ tier }}
  ports:
  - protocol: TCP
    port: 9000
---
apiVersion: v1
kind: Service
metadata:
  name: reproserver-registry-{{ tier }}
  labels:
    app: reproserver
    tier: {{ tier }}
spec:
  selector:
    app: reproserver
    repro-pod: registry
    tier: {{ tier }}
  ports:
  - protocol: TCP
    port: 5000
---
apiVersion: v1
kind: Service
metadata:
  name: reproserver-postgres-{{ tier }}
  labels:
    app: reproserver
    tier: {{ tier }}
spec:
  selector:
    app: reproserver
    repro-pod: postgres
    tier: {{ tier }}
  ports:
  - protocol: TCP
    port: 5432
