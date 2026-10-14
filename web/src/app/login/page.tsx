import { redirect } from "next/navigation";
import { LoginForm } from "@/components/login-form";
import { currentProducer } from "@/lib/session";

export default async function LoginPage() {
  if (await currentProducer()) redirect("/app");
  return <LoginForm />;
}
