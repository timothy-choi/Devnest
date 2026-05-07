import type { GetServerSideProps } from "next";

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: { destination: "/signup", permanent: false },
});

export default function RegisterRedirectPage() {
  return null;
}
