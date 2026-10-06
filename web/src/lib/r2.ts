import "server-only";

import { DeleteObjectsCommand, GetObjectCommand, HeadObjectCommand, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

// Cloudflare R2 through its S3-compatible API. The bucket is private: browsers only ever see
// short-lived signed URLs, and only after the server has checked they're a member.
const bucket = process.env.R2_BUCKET!;

let client: S3Client | undefined;
function r2() {
  client ??= new S3Client({
    region: "auto",
    endpoint: `https://${process.env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
    credentials: { accessKeyId: process.env.R2_ACCESS_KEY_ID!, secretAccessKey: process.env.R2_SECRET_ACCESS_KEY! },
    // Newer SDKs add checksum parameters to every upload URL by default; browsers can't send them.
    requestChecksumCalculation: "WHEN_REQUIRED",
    responseChecksumValidation: "WHEN_REQUIRED",
  });
  return client;
}

// An upload link for exactly `size` bytes: R2 rejects a different-sized body.
export function signUpload(key: string, contentType: string, size: number, expiresIn = 3600) {
  return getSignedUrl(r2(), new PutObjectCommand({ Bucket: bucket, Key: key, ContentType: contentType, ContentLength: size }), {
    expiresIn,
    signableHeaders: new Set(["content-type"]),
  });
}

export function signDownload(key: string, expiresIn = 3600) {
  return getSignedUrl(r2(), new GetObjectCommand({ Bucket: bucket, Key: key }), { expiresIn });
}

// Size in bytes, or null if the object isn't there.
export async function objectSize(key: string): Promise<number | null> {
  try {
    const head = await r2().send(new HeadObjectCommand({ Bucket: bucket, Key: key }));
    return head.ContentLength ?? null;
  } catch (e) {
    if ((e as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode === 404) return null;
    throw e;
  }
}

// Missing keys are fine: R2 treats deleting a missing object as success.
export async function deleteObjects(keys: string[]) {
  if (keys.length === 0) return;
  const res = await r2().send(new DeleteObjectsCommand({ Bucket: bucket, Delete: { Objects: keys.map((Key) => ({ Key })), Quiet: true } }));
  if (res.Errors?.length) throw new Error(`R2 delete failed for ${res.Errors.map((e) => e.Key).join(", ")}`);
}
