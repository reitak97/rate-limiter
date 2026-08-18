# Everything below is created in a single AWS account/region — no
# multi-region or multi-AZ HA is being attempted here.
provider "aws" {
    region = "us-east-1"
}

# Docker registry the app image gets pushed to (docker build && docker push),
# and that the task definition below pulls from at deploy time.
resource "aws_ecr_repository" "app" {
    name = "rate-limiter"
}

# Just a logical namespace for ECS services/tasks to run in — Fargate means
# no EC2 instances to manage underneath it.
resource "aws_ecs_cluster" "main" {
  name = "rate-limiter-cluster"
}

# The role ECS itself assumes to launch your task (pull the image, write
# logs) — distinct from any role your application code would assume to call
# AWS APIs. assume_role_policy is *who* can assume this role: only the ECS
# tasks service, via sts:AssumeRole.
resource "aws_iam_role" "ecs_task_execution" {
  name = "ecs-task-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}


# Grants that role permission to create/write CloudWatch Logs, which is what
# the container's `awslogs` log driver (see container_definitions below)
# needs in order to ship stdout/stderr off the container.
resource "aws_iam_role_policy" "ecs_logs" {
  name = "ecs-logs-policy"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "*"
    }]
  })
}


# AWS's own managed policy covering the baseline "pull image from ECR, write
# to the log group" permissions every ECS task execution role needs — layered
# on top of the custom logs policy above (which is technically redundant with
# this, since AmazonECSTaskExecutionRolePolicy already includes logs:*).
resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Reuses the AWS-provided default VPC/subnets instead of defining custom
# networking — reasonable for a demo, but means you're deploying into
# whatever that account's default network happens to look like.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Firewall for the task's network interface (network_mode = "awsvpc" below
# gives the task its own ENI, so this applies directly to it).
resource "aws_security_group" "app" {
  name   = "rate-limiter-sg"
  vpc_id = data.aws_vpc.default.id

  # Port 8000 open to the entire internet — there's no load balancer in
  # front, so this security group is the only thing gatekeeping access.
  # Fine for a portfolio demo; in production you'd typically put an ALB in
  # front and restrict this to the ALB's security group.
  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # All outbound traffic allowed (protocol "-1" = all protocols/ports) —
  # needed for the app container to reach ECR/CloudWatch etc.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# The blueprint for a task (one or more containers deployed together). This
# is where "app + redis" get wired into a single Fargate task.
resource "aws_ecs_task_definition" "app" {
  family                   = "rate-limiter"
  requires_compatibilities = ["FARGATE"]
  # "awsvpc" gives the whole task one shared network interface/namespace, so
  # its containers can reach each other over localhost — that's exactly why
  # REDIS_HOST=localhost works below, unlike docker-compose.yml where the app
  # reaches redis by the service hostname `redis` instead.
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([
  {
    name  = "app"
    # Hardcoded account ID + ":latest" tag: every deploy pulls whatever
    # "latest" currently points to in ECR, so redeploying this task
    # definition unchanged won't necessarily redeploy new code — ECS only
    # notices a change if the task definition itself changes. No image
    # digest pinning here.
    image = "677008162600.dkr.ecr.us-east-1.amazonaws.com/rate-limiter:latest"
    portMappings = [{ containerPort = 8000 }]
    environment = [{ name = "REDIS_HOST", value = "localhost" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/rate-limiter"
        "awslogs-region"        = "us-east-1"
        "awslogs-stream-prefix" = "ecs"
        # Lets ECS create the log group itself instead of requiring it to
        # already exist (and the execution role above grants CreateLogGroup
        # for exactly this reason).
        "awslogs-create-group"  = "true"
      }
    }
  },
  {
    name  = "redis"
    image = "redis:7-alpine"
    portMappings = [{ containerPort = 6379 }]
    # No volume/EFS mount here, so Redis's data directory lives on the
    # task's ephemeral storage: every redeploy or task restart wipes all
    # rate-limit buckets, and there's no AOF/RDB persistence configured
    # anyway. Fine for rate-limit counters (they're meant to be transient),
    # but worth being able to say out loud in an interview.
  }
])

}

# The thing that actually keeps a task running — the task definition above
# is just a template; this is what launches and supervises instances of it.
resource "aws_ecs_service" "app" {
  name            = "rate-limiter-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  # Single replica — no redundancy. If this task dies, ECS starts a
  # replacement, but there's a gap with zero capacity, and app+redis running
  # as one task means an app crash takes redis's in-memory buckets down with
  # it anyway.
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.app.id]
    # Gives the task a public IP directly (since there's no ALB routing in).
    assign_public_ip = true
  }
}
