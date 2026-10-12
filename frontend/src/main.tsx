import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router-dom"
import { Toaster } from "sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import "./index.css"
import App from "./App"

document.documentElement.classList.add("dark")
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <TooltipProvider delayDuration={300}>
        <App />
        <Toaster theme="dark" position="bottom-right" />
      </TooltipProvider>
    </BrowserRouter>
  </StrictMode>,
)
