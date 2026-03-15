with Arthexis.ORM;

package Apps.Fixtures.Functions.Fixtures_Functions is
   --  SQL-callable function registrations for the Fixtures app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register fixture-index views and seed fixture classes.
end Apps.Fixtures.Functions.Fixtures_Functions;
