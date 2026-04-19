import type { GetServerSideProps } from "next";

import LandingPage from "@/app/(marketing)/page";

export const getServerSideProps: GetServerSideProps = async () => {
  return { props: {} };
};

export default LandingPage;
