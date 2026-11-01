import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: (count, error) => count < 2 && !(error instanceof Error && "status" in error && (error as { status: number }).status < 500),
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
    },
  },
});
