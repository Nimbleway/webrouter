# AWS AgentCore Web Search: setup

`agentcore` searches through an AgentCore Gateway in your own AWS account, so you
create one gateway once, and from then on the call is the same as for any other
backend. You need the AWS CLI v2, an AWS credential allowed to create IAM roles and
AgentCore gateways, and a region where the connector runs: `us-east-1`,
`us-east-2`, `us-west-2`, `eu-west-1`, `ap-southeast-2` or `ap-northeast-1`.

AWS bills the account: $7 per 1,000 queries plus $0.005 per 1,000 gateway
invocations, with no charge for an idle gateway
([pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)). Its terms ask you
to show each result's citation and not to store results in bulk or build an index
from them.

## 1. Install

```bash
pip install "web-router[aws]"
```

## 2. Create the gateway

```bash
export AWS_REGION=us-east-1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

# The role the gateway assumes to call the search tool.
cat > trust.json <<EOF
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
  "Condition": {"StringEquals": {"aws:SourceAccount": "$ACCOUNT"},
                "ArnLike": {"aws:SourceArn": "arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:gateway/*"}}}]}
EOF
cat > websearch.json <<EOF
{"Version": "2012-10-17", "Statement": [
  {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeGateway",
   "Resource": "arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT:gateway/*"},
  {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeWebSearch",
   "Resource": "arn:aws:bedrock-agentcore:$AWS_REGION:aws:tool/web-search.v1"}]}
EOF
aws iam create-role --role-name webrouter-agentcore --assume-role-policy-document file://trust.json
aws iam put-role-policy --role-name webrouter-agentcore --policy-name websearch --policy-document file://websearch.json

# The gateway, which takes requests signed with your AWS credential.
GATEWAY_ID=$(aws bedrock-agentcore-control create-gateway --name webrouter \
  --role-arn "arn:aws:iam::$ACCOUNT:role/webrouter-agentcore" \
  --protocol-type MCP --authorizer-type AWS_IAM \
  --query gatewayId --output text)

# The web search tool, pinned to the connector version that takes filters.
aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$GATEWAY_ID" \
  --name web-search \
  --target-configuration '{"mcp":{"connector":{"source":{"connectorId":"web-search","version":"1.2.0"},"configurations":[{"name":"WebSearch","parameterValues":{}}]}}}' \
  --credential-provider-configurations '[{"credentialProviderType":"GATEWAY_IAM_ROLE"}]'
```

Wait until the target reports `READY` (about 30 seconds), then print the URL:

```bash
aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GATEWAY_ID" --query 'items[].status'
aws bedrock-agentcore-control get-gateway --gateway-identifier "$GATEWAY_ID" --query gatewayUrl --output text
```

## 3. Use it

Export the URL. The library reads the process environment, not a `.env` file. The MCP server loads `AGENTCORE_GATEWAY_URL` from its `.env` but never AWS keys, by design: it finds those through botocore, in your `~/.aws` profile or the environment it was started with.

```bash
export AGENTCORE_GATEWAY_URL=https://<id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
```

Whoever calls it needs `bedrock-agentcore:InvokeGateway` on the gateway, granted to
their own IAM user or role: the policy above is the gateway's, not the caller's, and
creating a gateway does not grant calling it. For example:

```bash
GATEWAY_ARN=$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GATEWAY_ID" --query gatewayArn --output text)
aws iam put-user-policy --user-name <you> --policy-name invoke-webrouter-gateway --policy-document \
  "{\"Version\": \"2012-10-17\", \"Statement\": [{\"Effect\": \"Allow\", \"Action\": \"bedrock-agentcore:InvokeGateway\", \"Resource\": \"$GATEWAY_ARN\"}]}"
```

Check it works by naming the backend, since `auto` tries AgentCore only after its
keyless members:

```python
from web_router import Provider, WebRouter

response = WebRouter(Provider.AGENTCORE).search("python 3.13 release", max_results=5, include_domains=["python.org"])
print(response.provider, [result.url for result in response.results])
```

A date range is AgentCore's own filter:

```python
WebRouter(Provider.AGENTCORE).search(
    "nvidia earnings",
    provider_options={"filters": {"publishedDateFilter": {"from": "2026-10-01T00:00:00Z"}}},
)
```

## 4. Tear it down

```bash
TARGET_ID=$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GATEWAY_ID" --query 'items[0].targetId' --output text)
aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GATEWAY_ID" --target-id "$TARGET_ID"
aws bedrock-agentcore-control delete-gateway --gateway-identifier "$GATEWAY_ID"
aws iam delete-user-policy --user-name <you> --policy-name invoke-webrouter-gateway
aws iam delete-role-policy --role-name webrouter-agentcore --policy-name websearch
aws iam delete-role --role-name webrouter-agentcore
```

## When it fails

| Error | Cause |
| --- | --- |
| `needs a gateway_url` | `AGENTCORE_GATEWAY_URL` is not set |
| `No AWS credentials found` | botocore found no profile, role or `AWS_*` variables |
| `needs exactly one` tool | the gateway has no web-search target, or two |
| `Pin the target to connector version 1.2.0` | the target predates filters, so domain lists and a date range are refused |
| HTTP 403 | the caller lacks `bedrock-agentcore:InvokeGateway` on the gateway (step 3), or the gateway role lacks `InvokeWebSearch` |
