# Guidance for Integrating Amazon DynamoDB and Amazon OpenSearch Service

## Table of Content

### Required

1. [Overview](#overview)
    - [Cost](#cost)
2. [Prerequisites](#prerequisites)
    - [Operating System](#operating-system)
3. [Deployment Steps](#deployment-steps-required)
4. [Deployment Validation](#deployment-validation-required)
5. [Running the Guidance](#running-the-guidance-required)
6. [Next Steps](#next-steps-required)
7. [Cleanup](#cleanup-required)

***Optional***

8. [FAQ, known issues, additional considerations, and limitations](#faq-known-issues-additional-considerations-and-limitations-optional)
9. [Revisions](#revisions-optional)
10. [Notices](#notices-optional)
11. [Authors](#authors-optional)

## Overview

Different databases are designed for different use cases. With these purpose-built databases, developers no longer need to choose a general purpose database that can do many things, but is not perfectly suited to any one particular task. Instead, they can choose a set of databases that are built specifically for the requirements of their application. Integrating those databases together allows developers to keep a consistent set of data that can be queried from the most appropriate database for the access pattern.

Amazon DynamoDB is a serverless, NoSQL, fully managed database with single-digit millisecond performance at any scale. It is ideally suited to support online transaction processing (OLTP) workloads where access patterns are known. These fixed access patterns often make up the bulk of an applications database requests, but other access patterns such as search require more flexibility and can tolerate higher latency.

To support these use cases, DynamoDB is often paired with Amazon OpenSearch Service. OpenSearch Service makes it easy for you to perform interactive log analytics, real-time application monitoring, website search, and more. OpenSearch is an open source, distributed search and analytics suite derived from Elasticsearch.

Most customers interested in integrating DynamoDB and OpenSearch Service should use the managed feature [DynamoDB zero-ETL integration with Amazon OpenSearch Service](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/OpenSearchIngestionForDynamoDB.html). Amazon DynamoDB offers a zero-ETL integration with Amazon OpenSearch Service through the DynamoDB plugin for OpenSearch Ingestion. Amazon OpenSearch Ingestion offers a fully managed, no-code experience for ingesting data into Amazon OpenSearch Service.

In some specific cases, though, customers may need additional flexibility in their integration. Examples of those cases include:

* Needing to reduce the number of DynamoDB Streams consumers

DynamoDB Streams supports up to two simultaneous consumers per shard. For DynamoDB global tables, simultaneous readers is limited to one. DynamoDB zero-ETL integration with Amazon OpenSearch Service is an additional shard reader. Choosing to use both results in no additional shard capacity to support other event driven business processes. Instead, a service like Amazon SNS or Amazon EventBridge can be used fan out to multiple consuming applications including a self managed DynamoDB to OpenSearch Service integration.

* Certain DynamoDB Single Table designs

While Amazon OpenSearch Ingestion supports processors that can different DynamoDB items to different OpenSearch indexes based on key prefix, it cannot combine multiple DynamoDB items into a single OpenSearch document. This requirement may come up if your DynamoDB design uses multiple DynamoDB items to represent the contents of a list or array, such as products in a shopping cart. DynamoDB [overloaded GSI](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-gsi-overloading.html) designs also use multiple DynamodB items to represent what would be a single OpenSearch document.

* Inability to use DynamoDB Point-in-time Recovery PITR

Enabling DynamoDB PITR is required in order to move existing data from DynamoDB to OpenSearch Service when using zero-ETL integration. While enabling PITR on all production tables is a best practice, if your organization disallows the use of PITR a different method of migrating existing data is required.

This guidance provides an example of a customer-managed integration between DynamoDB and OpenSearch Service. It creates a DynamoDB table with sample data, migrates that existing DynamoDB data to OpenSearch, and subscribes a Lambda function to DynamoDB Streams to replicate new data as it is written.

   ![Architecture](./assets/images/architecture-initial.png)

   To load the initial set of data from DynamoDB, the following steps are executed.

1. To process existing data, an AWS Lambda function is invoked to describe the Amazon DynamoDB table and split into a number of segments based on the returned item count. The function writes one message to an Amazon Simple Queue Service (SQS) queue for each segment number.

1. SQS acts as an event source for Lambda. Lambda will invoke functions from messages in the queue and process segments of the DynamoDB table in parallel.

1. The Lambda function uses a parallel scan to read the segment of the DynamoDB table listed in the source event from SQS. 

1. The function then writes the data retrieved from DynamoDB in to Amazon OpenSearch Service in batches through the bulk create operation. 

   ![Architecture](./assets/images/architecture-initial.png)

   To support the ongoing load of data from DynamoDB as it is written, the following steps are executed.

1. Insert or update items in Amazon DynamoDB to invoke capture by Amazon DynamoDB Streams.

1. DynamoDB Streams sends item-level modifications captured from DynamoDB to the AWS Lambda streaming update function.

1. The Lambda function writes that data in batches to Amazon OpenSearch Service through the bulk index operation. Track ingested documents with the SearchableDocuments metric in Amazon CloudWatch.

### Cost

_You are responsible for the cost of the AWS services used while running this Guidance. As of June 2024, the cost for running this Guidance with the default settings in the US West2 (Oregon) is approximately $<n.nn> per month for processing ( <nnnnn> records )._

_We recommend creating a [Budget](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html) through [AWS Cost Explorer](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/) to help manage costs. Prices are subject to change. For full details, refer to the pricing webpage for each AWS service used in this Guidance._

### Sample Cost Table

**Note : Once you have created a sample cost table using AWS Pricing Calculator, copy the cost breakdown to below table and upload a PDF of the cost estimation on BuilderSpace.**

The following table provides a sample cost breakdown for deploying this Guidance with the default parameters in the US East (N. Virginia) Region for one month.

| AWS service  | Dimensions | Cost [USD] |
| ----------- | ------------ | ------------ |
| Amazon API Gateway | 1,000,000 REST API calls per month  | $ 3.50month |
| Amazon Cognito | 1,000 active users per month without advanced security feature | $ 0.00 |

## Prerequisites

Make sure you have the following tools installed on your environment:

* [AWS Command Line Interface (CLI)](https://aws.amazon.com/cli/)
* [Node.js](https://nodejs.org/en/download/) 14.15.0 or later
* [Python](https://www.python.org/downloads/) 3.7 or later including pip and virtualenv
* [AWS Cloud Development Kit (AWS CDK)](https://docs.aws.amazon.com/cdk/v2/guide/getting_started.html)

### Operating System

These deployment instructions are optimized to best work on Amazon Linux 2023 (ami-0eb9d67c52f5c80e5).  Deployment in another OS may require additional steps.

### aws cdk bootstrap (if sample code has aws-cdk)

<If using aws-cdk, include steps for account bootstrap for new cdk users.>

**Example blurb:** “This Guidance uses aws-cdk. If you are using aws-cdk for first time, please perform the below bootstrapping....”

## Deployment Steps (required)

Deployment steps must be numbered, comprehensive, and usable to customers at any level of AWS expertise. The steps must include the precise commands to run, and describe the action it performs.

* All steps must be numbered.
* If the step requires manual actions from the AWS console, include a screenshot if possible.
* The steps must start with the following command to clone the repo. ```git clone xxxxxxx```
* If applicable, provide instructions to create the Python virtual environment, and installing the packages using ```requirement.txt```.
* If applicable, provide instructions to capture the deployed resource ARN or ID using the CLI command (recommended), or console action.

 
1. Clone the repo using command ```git clone xxxxxxxxxx```
1. cd to the repo folder ```cd <repo-name>```
1. Install packages in requirements using command ```pip install requirement.txt```
1. Edit content of **file-name** and replace **s3-bucket** with the bucket name in your account.
1. Run this command to deploy the stack ```cdk deploy``` 
1. Capture the domain name created by running this CLI command ```aws apigateway ............```



## Deployment Validation  (required)

<Provide steps to validate a successful deployment, such as terminal output, verifying that the resource is created, status of the CloudFormation template, etc.>


**Examples:**

* Open CloudFormation console and verify the status of the template with the name starting with xxxxxx.
* If deployment is successful, you should see an active database instance with the name starting with <xxxxx> in        the RDS console.
*  Run the following CLI command to validate the deployment: ```aws cloudformation describe xxxxxxxxxxxxx```



## Running the Guidance (required)

<Provide instructions to run the Guidance with the sample data or input provided, and interpret the output received.> 

This section should include:

* Guidance inputs
* Commands to run
* Expected output (provide screenshot if possible)
* Output description



## Next Steps (required)

This guidance provides a basic example of a DynamoDB integration with OpenSearch through DynamoDB Streams and Lambda. In a production environment, a similar solution should take several things into consideration. Here are some additional questions and considerations.

* Add any additional transformations in the replicating Lambda functions. If your application uses a design that requires aggregation or augmentation before documents are written to OpenSearch include that logic in the replication functions.
* Follow [operational best practices for Amazon OpenSearch service](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/bp.html#bp-stability). This guidance provisions a single node to provide a low cost demonstration. A production environment should leverage dedicated master nodes and deploy across multiple availability zones.
* Use a more robust access proxy for OpenSearch. This guidance uses a single nginx proxy to access OpenSearch dashboards. A production environment should deploy a [more scalable solution](https://docs.aws.amazon.com/solutions/latest/centralized-logging-with-opensearch/access-proxy-1.html) with autoscaling proxies and a load balancer.



## Cleanup (required)

To cleanup installed resources, run `cdk destroy`.


## FAQ, known issues, additional considerations, and limitations

**Additional considerations**

- This Guidance created an Amazon OpenSearch Service cluster and Amazon Elastic Compute Cloud (Amazon EC2) instance that are billed per hour irrespective of usage. Clean up resources when you are finished to avoid unexpected costs.

For any feedback, questions, or suggestions, please use the issues tab under this repo.

## Notices

*Customers are responsible for making their own independent assessment of the information in this Guidance. This Guidance: (a) is for informational purposes only, (b) represents AWS current product offerings and practices, which are subject to change without notice, and (c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided “as is” without warranties, representations, or conditions of any kind, whether express or implied. AWS responsibilities and liabilities to its customers are controlled by AWS agreements, and this Guidance is not part of, nor does it modify, any agreement between AWS and its customers.*


## Authors

John Terhune
Jon Handler