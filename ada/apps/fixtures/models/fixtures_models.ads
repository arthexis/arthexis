with Arthexis.ORM;

package Apps.Fixtures.Models.Fixtures_Models is
   --  Schema objects owned by the Fixtures app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Create fixture class and bundle metadata tables.
end Apps.Fixtures.Models.Fixtures_Models;
