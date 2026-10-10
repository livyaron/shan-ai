import { useState, useEffect } from "react";
import { Download, Smartphone, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

const Install = () => {
  const [deferredPrompt, setDeferredPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [isInstalled, setIsInstalled] = useState(false);

  useEffect(() => {
    const handler = (e: Event) => {
      e.preventDefault();
      setDeferredPrompt(e as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", handler);

    if (window.matchMedia("(display-mode: standalone)").matches) {
      setIsInstalled(true);
    }

    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  const handleInstall = async () => {
    if (!deferredPrompt) return;
    await deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    if (outcome === "accepted") setIsInstalled(true);
    setDeferredPrompt(null);
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4" dir="rtl">
      <Card className="max-w-md w-full border-0 shadow-lg">
        <CardContent className="p-8 text-center space-y-6">
          <div className="w-20 h-20 rounded-2xl bg-primary mx-auto flex items-center justify-center">
            <Smartphone className="w-10 h-10 text-primary-foreground" />
          </div>

          <div>
            <h1 className="text-2xl font-heading font-bold">מערכת לקחים</h1>
            <p className="text-muted-foreground mt-2">
              התקן את האפליקציה במכשיר שלך לגישה מהירה ועבודה אופליין
            </p>
          </div>

          {isInstalled ? (
            <div className="flex items-center justify-center gap-2 text-success">
              <CheckCircle2 className="w-5 h-5" />
              <span className="font-medium">האפליקציה מותקנת!</span>
            </div>
          ) : deferredPrompt ? (
            <Button onClick={handleInstall} size="lg" className="w-full gap-2">
              <Download className="w-5 h-5" />
              התקן אפליקציה
            </Button>
          ) : (
            <div className="space-y-4 text-sm text-muted-foreground">
              <p className="font-medium text-foreground">להתקנה ידנית:</p>
              <div className="text-right space-y-2">
                <p>📱 <strong>iPhone:</strong> לחץ על "שתף" → "הוסף למסך הבית"</p>
                <p>📱 <strong>Android:</strong> לחץ על תפריט הדפדפן → "התקן אפליקציה"</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default Install;
