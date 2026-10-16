import { supabase } from "@/integrations/supabase/client";

/**
 * Uploading a lesson's photo or file from a phone (PLAN-lessons-mobile.md M1).
 * A 4–8 MB camera photo on a weak signal is a silent failure, so photos are
 * shrunk on the phone first: longest side MAX_SIDE px, JPEG at JPEG_QUALITY.
 */
export const MAX_SIDE = 1600;
export const JPEG_QUALITY = 0.82;
const BUCKET = "lesson-files";

/** The target size for a w×h image, keeping its ratio. */
export const fitWithin = (w: number, h: number, max: number = MAX_SIDE): { w: number; h: number } => {
  const longest = Math.max(w, h);
  if (longest <= max) return { w, h };
  const k = max / longest;
  return { w: Math.round(w * k), h: Math.round(h * k) };
};

/** A smaller JPEG of an image; anything else (or a failure) comes back unchanged. */
export const compressImage = async (file: File): Promise<File> => {
  if (!file.type.startsWith("image/") || file.type === "image/gif" || file.type === "image/svg+xml") return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" } as ImageBitmapOptions);
    const { w, h } = fitWithin(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.drawImage(bitmap, 0, 0, w, h);
    bitmap.close?.();
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
    if (!blob || blob.size >= file.size) return file;
    const name = file.name.replace(/\.[^.]+$/, "") || "photo";
    return new File([blob], `${name}.jpg`, { type: "image/jpeg" });
  } catch {
    return file;
  }
};

/** Uploads (photos compressed first) and returns the public URL; throws on failure. */
export const uploadLessonFile = async (file: File): Promise<string> => {
  const ready = await compressImage(file);
  const ext = ready.name.includes(".") ? ready.name.split(".").pop() : "bin";
  const path = `${Date.now()}_${Math.random().toString(36).slice(2)}.${ext}`;
  const { error } = await supabase.storage.from(BUCKET).upload(path, ready);
  if (error) throw error;
  return supabase.storage.from(BUCKET).getPublicUrl(path).data.publicUrl;
};

const IMAGE_EXT = /\.(jpe?g|png|gif|webp|heic|heif)(\?|#|$)/i;

/** An attachment the phone shows as a picture (by its name — the URL carries no type). */
export const isImageUrl = (url: string | undefined): boolean => !!url && IMAGE_EXT.test(url);
