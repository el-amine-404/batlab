/**
 * The account pages' reads (people, keys, one's account), through TanStack
 * Query: answers kept between screens. The job list has its own poll
 * (jobs.tsx), whose answer the browser revalidates by ETag.
 */
import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (count, error) => count < 2 && !(error instanceof Error && "status" in error && (error as { status: number }).status < 500),
      refetchOnWindowFocus: true,
    },
  },
});
